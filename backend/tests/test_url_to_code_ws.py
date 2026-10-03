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


@pytest.fixture(autouse=True)
def signed_in(client: TestClient, tmp_path: Any) -> None:
    """Every run needs an account now, so give each test one.

    Signed in rather than allowed through: the point of these tests is what
    a run does once it is allowed to happen, and letting them past a limit
    would test a path no user can reach.
    """
    import accounts as accounts_module
    from routes import accounts as accounts_route

    accounts_module.DB_PATH = tmp_path / "accounts.db"
    accounts_module._initialised = False
    account = accounts_module.register("ws@test.dev", "correct horse battery")
    client.cookies.set(
        accounts_route.SESSION_COOKIE,
        accounts_route._sign(account.id, __import__("time").time()),
    )


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
    # Both cleared: a model in .env is a deliberate choice, and this is
    # about what happens when nothing at all was chosen.
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", None)
    monkeypatch.setattr(url_to_code, "OPENROUTER_MODEL", None, raising=False)

    cfg = url_to_code._llm_config_for(
        url_to_code.UrlToCodeParams(
            url="https://example.com", openrouter_api_key="test-key"
        )
    )

    # The route leaves the id empty and the client fills in the default, so
    # both halves of that contract are checked here.
    assert cfg.provider == "openrouter" and cfg.model == ""
    assert llm_http._openai_style_model(cfg) == llm_http.DEFAULT_OPENROUTER_MODEL
    # The ":free" entry stayed in the catalog after the tier was retired, so
    # every default run failed with a 404 that read like a model error.
    assert ":free" not in llm_http.DEFAULT_OPENROUTER_MODEL


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
    assert any(a.startswith("single-file-retry") for a in attempts)
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


def _confirming_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, chosen: List[str]
) -> List[str]:
    """A client that answers the page-selection question, and records the order.

    The recording is how the money is checked: the paths the model was asked
    for are the paths the user picked, and nothing else.
    """
    asked: List[str] = []

    async def crawl_many(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(
                    url=f"{start_url}{path}",
                    path=path,
                    title=path,
                    html="<html><body>hi</body></html>",
                )
                for path in ("/", "/about", "/blog/post-1", "/login")
            ],
        )

    async def generate(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        asked.append(event_prefix)
        return Completion(text="<!DOCTYPE html><html><body>page</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_many)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", generate)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", generate)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")
    return asked


def _await_question(ws: Any) -> Dict[str, Any]:
    """Read up to the page-selection question, skipping the progress before it.

    The server says what it is doing first, and a test that assumed the very
    next message was the question would break the moment another status line
    was added - without ever showing why.
    """
    from starlette.websockets import WebSocketDisconnect

    while True:
        try:
            message = ws.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            raise AssertionError("the server never asked which pages to build")
        if message.get("type") == "pageSelection":
            return message


def test_only_the_pages_the_user_picked_are_generated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crawler that found four pages does not mean four model calls."""
    asked = _confirming_client(client, monkeypatch, ["/", "/about"])

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "confirmPages": True}))
        question = _await_question(ws)
        assert [entry["path"] for entry in question["data"]["pages"]] == [
            "/",
            "/about",
            "/blog/post-1",
            "/login",
        ]
        ws.send_text(json.dumps({"type": "pageSelection", "paths": ["/", "/about"]}))
        messages = _drain(ws)

    # Two of the four. The blog post and the login screen are never asked for.
    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]
    assert "/" in code
    assert "/about" in code
    assert "/blog/post-1" not in code
    assert "/login" not in code
    assert asked


def test_a_run_with_no_pages_picked_stops_without_asking_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _confirming_client(client, monkeypatch, [])

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "confirmPages": True}))
        _await_question(ws)
        ws.send_text(json.dumps({"type": "pageSelection", "paths": []}))
        messages = _drain(ws)

    # The user looked at the list and chose none of it. Generating anyway
    # would spend their money on a decision they did not make.
    assert asked == []
    assert any(m["type"] == "status" and "No pages selected" in m["value"] for m in messages)
    assert not any(m["type"] == "setCode" for m in messages)


def test_a_client_that_never_answers_does_not_get_the_whole_site(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _confirming_client(client, monkeypatch, [])
    monkeypatch.setattr(url_to_code, "PAGE_SELECTION_TIMEOUT_SECONDS", 0.05)

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "confirmPages": True}))
        _await_question(ws)  # the question, then silence
        messages = _drain(ws)

    assert asked == []
    assert not any(m["type"] == "setCode" for m in messages)


def test_a_site_of_one_page_is_built_without_asking(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question with one possible answer is an obstacle, not a choice."""

    async def crawl_one(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(base_url=start_url, pages=_pages(1))

    async def generate(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>page</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_one)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", generate)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", generate)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test", "confirmPages": True}))
        messages = _drain(ws)

    assert not any(m["type"] == "pageSelection" for m in messages)
    assert any(m["type"] == "setCode" for m in messages)


def test_the_step_is_off_unless_asked_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _confirming_client(client, monkeypatch, [])

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://site.test"}))
        messages = _drain(ws)

    # Generating straight away is what a user who never asked for the step
    # expects; a modal they did not ask for is worse than no step at all.
    assert not any(m["type"] == "pageSelection" for m in messages)
    assert any(m["type"] == "setCode" for m in messages)


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


def test_nextjs_stack_returns_page_components_without_portal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A framework stack yields one component per route, even with singleFile."""

    async def crawl_two(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(url=f"{start_url}{path}", path=path, title=path, html="<html/>")
                for path in ("/", "/about")
            ],
        )

    async def fail_if_called(*_args: Any, **_kwargs: Any) -> Completion:
        raise AssertionError("no portal or single file for a framework stack")

    async def page(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(
            text='```tsx\n"use client";\nexport default function Page() {\n'
            "  return <main>page</main>;\n}\n```"
        )

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_two)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", fail_if_called)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", page)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(
            json.dumps(
                {"url": "https://example.com", "stack": "nextjs_tailwind", "singleFile": True}
            )
        )
        messages = _drain(ws)

    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]

    assert set(code) == {"/", "/about"}
    assert 'data-framework="nextjs"' in code["/about"]
    assert 'data-route="/about"' in code["/about"]
    assert "export default function Page()" in code["/about"]


def test_the_retry_allowance_is_handed_out_once() -> None:
    budget = url_to_code.RetryBudget(2)

    assert budget.take()
    assert budget.take()
    assert not budget.take()
    assert (budget.used, budget.remaining) == (2, 0)


def test_no_retries_means_no_extra_attempts() -> None:
    assert not url_to_code.RetryBudget(0).take()
    assert not url_to_code.RetryBudget(-1).take()


def test_max_retries_is_one_budget_for_the_whole_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advanced options offers one number, so the run gets one number of retries.

    The portal page spends it here; a page that later answers with a plan gets
    no second chance, instead of a fresh allowance of its own.
    """

    async def crawl_two(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(
            base_url=start_url,
            pages=[
                CrawlPage(url=f"{start_url}{path}", path=path, title=path, html="<html/>")
                for path in ("/", "/about")
            ],
        )

    calls: List[str] = []

    async def plan_only(
        _prompt: str, *_args: Any, event_prefix: str = "gen", **_kwargs: Any
    ) -> Completion:
        calls.append(event_prefix)
        return Completion(text="I will recreate this with divs:")

    async def page(page_data: Dict[str, Any], *_args: Any, **_kwargs: Any) -> Completion:
        path = str(page_data.get("path", "/"))
        calls.append(path)
        if path == "/":
            return Completion(text="<!DOCTYPE html><html><body>home</body></html>")
        return Completion(text="still planning")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_two)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", plan_only)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", page)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(
            json.dumps({"url": "https://example.com", "maxRetries": 1})
        )
        messages = _drain(ws)

    # One portal retry, then no retries left for the page that failed.
    assert calls.count("structure") == 1
    assert calls.count("structure-retry-1") == 1
    assert calls.count("/") == 1
    assert calls.count("/about") == 1
    assert "pageComplete" in [m["type"] for m in messages]
    failed = next(
        m
        for m in messages
        if m["type"] == "pageComplete" and m["data"]["path"] == "/about"
    )
    assert failed["data"]["error"] is True
    code = next(m for m in messages if m["type"] == "setCode")["data"]["code"]
    # No usable portal was generated, so the home page stands in for it.
    assert code["project-structure"] == code["/"]
