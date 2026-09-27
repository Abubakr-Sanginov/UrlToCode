import json
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.url_to_code as url_to_code
from crawler.crawler import CrawlPage, CrawlResult
import llm_http
from llm_http import Completion


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(url_to_code.router)
    return TestClient(app)


def _drain(ws: Any) -> List[Dict[str, Any]]:
    """Collect messages until the server closes the socket."""
    from starlette.websockets import WebSocketDisconnect

    received: List[Dict[str, Any]] = []
    while True:
        try:
            received.append(ws.receive_json())
        except WebSocketDisconnect:
            return received
        except RuntimeError:
            # TestClient raises this once the peer has gone away.
            return received


def test_missing_url_is_reported_before_any_work(client: TestClient) -> None:
    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "   "}))
        messages = _drain(ws)

    assert messages[0]["type"] == "error"
    assert "URL is required" in messages[0]["value"]


def test_no_provider_fails_fast_without_crawling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with no usable key must fail before spending time on a crawl."""
    for name in (
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setattr(url_to_code, name, None)

    crawled = False

    async def fail_if_called(self: Any, start_url: str) -> CrawlResult:  # pragma: no cover
        nonlocal crawled
        crawled = True
        return CrawlResult(base_url=start_url)

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", fail_if_called)

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        messages = _drain(ws)

    assert crawled is False
    assert messages[0]["type"] == "error"
    assert "No model provider configured" in messages[0]["value"]


def test_malformed_numbers_do_not_kill_the_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`maxPages: "abc"` used to raise ValueError and drop the socket."""
    seen: Dict[str, int] = {}

    original_init = url_to_code.SiteCrawler.__init__

    def record_init(self: Any, **kwargs: Any) -> None:
        seen.update(
            {"max_pages": kwargs["max_pages"], "max_depth": kwargs["max_depth"]}
        )
        original_init(self, **kwargs)

    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(
                    url=start_url,
                    path="/",
                    title="Example",
                    html="<html><body><h1>Hi</h1></body></html>",
                )
            ],
        )

    async def fake_llm(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>ok</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "__init__", record_init)
    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", fake_llm)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", fake_llm)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(
            json.dumps(
                {
                    "url": "https://example.com",
                    "maxPages": "abc",
                    "maxDepth": None,
                    "stack": "html-tailwind",
                }
            )
        )
        messages = _drain(ws)

    # Bad values fall back to the defaults instead of raising.
    assert seen == {
        "max_pages": url_to_code.DEFAULT_PAGES,
        "max_depth": url_to_code.DEFAULT_DEPTH,
    }
    assert not any(m["type"] == "error" for m in messages)

    set_code = next(m for m in messages if m["type"] == "setCode")
    assert set_code["data"]["code"]["project-structure"]


def test_out_of_range_numbers_are_clamped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: Dict[str, int] = {}
    original_init = url_to_code.SiteCrawler.__init__

    def record_init(self: Any, **kwargs: Any) -> None:
        seen.update(
            {"max_pages": kwargs["max_pages"], "max_depth": kwargs["max_depth"]}
        )
        original_init(self, **kwargs)

    async def crawl_empty(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(base_url=start_url, error="stop here")

    monkeypatch.setattr(url_to_code.SiteCrawler, "__init__", record_init)
    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_empty)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(
            json.dumps(
                {"url": "https://example.com", "maxPages": 9999, "maxDepth": 9999}
            )
        )
        _drain(ws)

    assert seen == {
        "max_pages": url_to_code.MAX_PAGES,
        "max_depth": url_to_code.MAX_DEPTH,
    }


def test_all_pages_failing_reports_an_error_instead_of_empty_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[CrawlPage(url=start_url, path="/", title="Example", html="<html/>")],
        )

    async def no_output(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(empty_reason="nothing came back")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", no_output)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", no_output)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        messages = _drain(ws)

    assert not any(m["type"] == "setCode" for m in messages)
    error = next(m for m in messages if m["type"] == "error")
    assert "Every page failed" in error["value"]


def test_provider_refusal_is_reported_verbatim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retired model or bad key must surface the provider's own message.

    Previously any provider rejection collapsed into a generic "failed to
    generate" line, so a 404 for a model OpenRouter had moved behind a paid
    plan was indistinguishable from a real generation failure.
    """

    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[CrawlPage(url=start_url, path="/", title="Example", html="<html/>")],
        )

    async def refuse(*_args: Any, **_kwargs: Any) -> Completion:
        raise url_to_code.ProviderError(
            "OpenRouter returned 404: This model is unavailable for free. "
            "Pick a different model in Settings."
        )

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", refuse)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        messages = _drain(ws)

    error = next(m for m in messages if m["type"] == "error")
    assert "404" in error["value"]
    assert "unavailable for free" in error["value"]
    assert "Pick a different model" in error["value"]


def test_provider_refusal_mid_run_aborts_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal during page generation applies to every page, so it aborts."""

    async def crawl_two(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(url=f"{start_url}{path}", path=path, title=path, html="<html/>")
                for path in ("/", "/about")
            ],
        )

    async def structure(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>portal</body></html>")

    async def refuse_page(*_args: Any, **_kwargs: Any) -> Completion:
        raise url_to_code.ProviderError("OpenRouter returned 429: rate limited.")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_two)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", structure)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", refuse_page)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com", "maxPages": 2}))
        messages = _drain(ws)

    assert not any(m["type"] == "setCode" for m in messages)
    error = next(m for m in messages if m["type"] == "error")
    assert "429" in error["value"]


def test_default_openrouter_model_is_used_when_client_sends_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", None)

    cfg = url_to_code._llm_config_for(
        url_to_code.UrlToCodeParams(
            url="https://example.com", openrouter_api_key="test-key"
        )
    )

    # The route leaves the id empty and the client fills in the default, so
    # both halves of that contract are checked here.
    assert cfg.provider == "openrouter" and cfg.model == ""
    assert llm_http._openai_style_model(cfg) == llm_http.DEFAULT_OPENROUTER_MODEL
    # A retired free tier here breaks every default run, so keep it explicit.
    assert llm_http.DEFAULT_OPENROUTER_MODEL.endswith(":free")


def test_every_generated_page_is_returned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frontend builds one version per entry, so none may be dropped."""

    async def crawl_three(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(url=f"{start_url}{path}", path=path, title=path, html="<html/>")
                for path in ("/", "/about", "/contact")
            ],
        )

    async def structure(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>portal</body></html>")

    async def page(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>page</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_three)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", structure)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", page)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com", "maxPages": 3}))
        messages = _drain(ws)

    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]

    assert set(code) == {"project-structure", "/", "/about", "/contact"}
    assert all(value.strip() for value in code.values())


def test_empty_single_file_error_explains_why(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[CrawlPage(url=start_url, path="/", title="Example", html="<html/>")],
        )

    async def no_output(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(
            empty_reason="OpenRouter hit the output token limit before writing any HTML",
            truncated=True,
        )

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", no_output)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com", "singleFile": True}))
        messages = _drain(ws)

    error = next(m for m in messages if m["type"] == "error")
    assert "usable single-file clone" in error["value"]
    assert "output token limit" in error["value"]


def test_bot_check_wall_is_reported_instead_of_cloned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatgpt.com answers the crawler with a Cloudflare "Just a moment" page.

    Handing that to the model produced an empty clone and a mystery error.
    """

    async def crawl_blocked(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(
                    url=start_url,
                    path="/",
                    title="Just a moment...",
                    html="<html></html>",
                    blocked=True,
                )
            ],
        )

    async def fail_if_called(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("generation must not start behind a bot check")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_blocked)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", fail_if_called)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://chatgpt.com", "singleFile": True}))
        messages = _drain(ws)

    error = next(m for m in messages if m["type"] == "error")
    assert "bot check" in error["value"]


def test_a_plan_is_not_shipped_as_the_clone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model narrating its approach is not a finished page.

    A run on YouTube delivered "For the masthead structure, I'll recreate
    with divs:" as the whole clone.
    """

    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[CrawlPage(url=start_url, path="/", title="YouTube", html="<html/>")],
        )

    attempts: List[str] = []

    async def narrate(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        attempts.append(event_prefix)
        return Completion(text="For the masthead structure, I'll recreate with divs:")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", narrate)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://youtube.com", "singleFile": True}))
        messages = _drain(ws)

    assert not any(m["type"] == "setCode" for m in messages)
    assert "single-file-retry" in attempts
    error = next(m for m in messages if m["type"] == "error")
    assert "commentary" in error["value"]
    assert "masthead" in error["value"]


def test_a_document_wrapped_in_narration_is_still_delivered() -> None:
    """Prose around the file must not cost the user the file."""
    answer = (
        "For the masthead structure, I'll recreate with divs:\n\n"
        "```html\n<!DOCTYPE html><html><body>ok</body></html>\n```\n"
        "Let me know if you want changes."
    )

    cleaned = url_to_code._clean_llm_output(answer)

    assert cleaned == "<!DOCTYPE html><html><body>ok</body></html>"
    assert url_to_code._is_usable_html(cleaned)


def test_the_largest_fenced_block_wins_over_the_first_snippet() -> None:
    """Answers that discuss a snippet first used to ship that snippet."""
    answer = (
        "First the nav:\n```html\n<nav>x</nav>\n```\n"
        "Now the file:\n```html\n<div>" + "y" * 200 + "</div>\n```\n"
    )

    assert url_to_code._clean_llm_output(answer).startswith("<div>")


def test_a_truncated_document_is_not_accepted() -> None:
    assert not url_to_code._is_usable_html("<!DOCTYPE html><html><body>cut off")


def _pages(count: int, html: str = "<html><body>hi</body></html>") -> List[CrawlPage]:
    return [
        CrawlPage(url=f"https://site.test/p{i}", path=f"/p{i}", title=f"P{i}", html=html)
        for i in range(count)
    ]


def test_a_large_site_is_generated_per_page_despite_the_single_file_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One call cannot emit a whole large site; it comes back truncated."""

    async def crawl_many(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(base_url=start_url, pages=_pages(6))

    prefixes: List[str] = []

    async def generate(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        prefixes.append(event_prefix)
        return Completion(text="<!DOCTYPE html><html><body>page</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_many)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", generate)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", generate)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "singleFile": True}))
        messages = _drain(ws)

    assert "single-file" not in prefixes
    assert "structure" in prefixes
    status = next(
        m for m in messages if m["type"] == "status" and "one file per page" in m["value"]
    )
    assert "6 pages" in status["value"]
    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]
    assert "/p0" in code and "/p5" in code


def test_a_small_site_still_gets_the_single_file_it_asked_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crawl_two(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(base_url=start_url, pages=_pages(2))

    prefixes: List[str] = []

    async def generate(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        prefixes.append(event_prefix)
        return Completion(text="<!DOCTYPE html><html><body>site</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_two)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", generate)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "singleFile": True}))
        messages = _drain(ws)

    assert prefixes == ["single-file"]
    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]
    assert code["/"].startswith("<!DOCTYPE html>")


def test_a_page_heavy_structure_also_falls_back() -> None:
    """Few pages can still carry more structure than one answer can emit."""
    result = CrawlResult(base_url="https://site.test", pages=_pages(2))

    assert url_to_code._single_file_too_large(result, "x" * 20_000)
    assert not url_to_code._single_file_too_large(result, "x" * 500)
    assert url_to_code._single_file_too_large(
        CrawlResult(base_url="https://site.test", pages=_pages(9)), "x" * 500
    )
