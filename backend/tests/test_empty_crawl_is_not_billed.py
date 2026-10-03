"""A crawl that reached nothing must not be billed as a clone.

Zero pages with no reported error used to fall through to "generate from
the URL alone": the model was paid to invent a page for a site that was
never loaded, and the user was charged for it. These tests pin both halves
- the refusal, and the allowance coming back.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import crawler.crawler as crawler_module
import routes.url_to_code as url_to_code
from crawler.crawler import CrawlResult


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(url_to_code.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def signed_in(client: TestClient, tmp_path: Any) -> Any:
    accounts_module.DB_PATH = tmp_path / "accounts.db"
    accounts_module._initialised = False
    account = accounts_module.register("empty-crawl@test.dev", "correct horse battery")
    client.cookies.set("utc_session", _sign(account.id))
    return account


def _sign(account_id: int) -> str:
    from routes import accounts as accounts_route
    import time

    return accounts_route._sign(account_id, time.time())


def _drain(ws: Any) -> List[Any]:
    from starlette.websockets import WebSocketDisconnect

    received: List[Any] = []
    while True:
        try:
            received.append(ws.receive_json())
        except (WebSocketDisconnect, RuntimeError):
            return received


def fake_crawler_returning(
    pages: List[Any], error: Optional[str] = None
) -> Any:
    empty_api: Dict[str, Any] = {}

    def substitute(*args: Any) -> Any:
        return pages, empty_api, error

    return substitute


class test_reaching_nothing_is_a_failure:
    def test_zero_pages_with_no_error_is_reported_as_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every navigation failed and the worker reported nothing, so the
        # result looked like a success with an empty page list.
        monkeypatch.setattr(
            crawler_module, "_run_playwright_subprocess", fake_crawler_returning([])
        )

        result = asyncio.run(
            crawler_module.SiteCrawler(max_pages=1, max_depth=0).crawl(
                "https://gone.invalid"
            )
        )

        assert result.pages == []
        assert result.error is not None
        assert "gone.invalid" in result.error

    def test_a_page_that_did_load_is_still_a_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            crawler_module,
            "_run_playwright_subprocess",
            fake_crawler_returning(
                [
                    {
                        "url": "https://example.com/",
                        "path": "/",
                        "title": "Example",
                        "html": "<html></html>",
                    }
                ]
            ),
        )

        result = asyncio.run(
            crawler_module.SiteCrawler(max_pages=1, max_depth=0).crawl(
                "https://example.com"
            )
        )

        assert len(result.pages) == 1
        assert result.error is None


class test_the_run_itself:
    @staticmethod
    def _refusing_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
        async def crawl_failing(self: Any, start_url: str) -> CrawlResult:
            return CrawlResult(
                base_url=start_url,
                error=f"Could not load {start_url}. The site did not respond.",
            )

        monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_failing)
        monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    def test_a_site_that_never_loaded_is_refused(
        self, client: TestClient, signed_in: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._refusing_crawl(monkeypatch)

        with client.websocket_connect("/url-to-code") as ws:
            ws.send_text(json.dumps({"url": "https://gone.invalid"}))
            messages = _drain(ws)

        errors = [m for m in messages if m["type"] == "error"]
        assert errors, f"expected a refusal, got {[m['type'] for m in messages]}"
        # And nothing was generated: the model must not be paid to invent
        # a page for a site that was never reached.
        assert not [
            m for m in messages if m["type"] in ("variantComplete", "complete")
        ]

    def test_the_unit_is_back_afterwards(
        self, client: TestClient, signed_in: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._refusing_crawl(monkeypatch)

        with client.websocket_connect("/url-to-code") as ws:
            ws.send_text(json.dumps({"url": "https://gone.invalid"}))
            _drain(ws)

        assert accounts_module.usage_of(signed_in, "generate").remaining == 1