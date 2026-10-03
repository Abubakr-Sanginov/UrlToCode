"""A finished clone becomes one of the account's projects by itself.

Nothing asked for it and nothing had to be clicked: the user paid for a
run, the run produced a site, and the site is theirs to come back to. The
accounts that ran something and had nothing listed are the bug this pins.
"""

import json
from typing import Any, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import routes.url_to_code as url_to_code
from crawler.crawler import CrawlPage, CrawlResult
from llm_http import Completion


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(url_to_code.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def signed_in(client: TestClient, tmp_path: Any) -> Any:
    accounts_module.DB_PATH = tmp_path / "accounts.db"
    accounts_module._initialised = False
    account = accounts_module.register("auto@test.dev", "correct horse battery")
    from routes import accounts as accounts_route
    import time

    client.cookies.set(accounts_route.SESSION_COOKIE, accounts_route._sign(account.id, time.time()))
    return account


def _drain(ws: Any) -> List[Any]:
    from starlette.websockets import WebSocketDisconnect

    received: List[Any] = []
    while True:
        try:
            received.append(ws.receive_json())
        except (WebSocketDisconnect, RuntimeError):
            return received


def _one_page() -> CrawlResult:
    return CrawlResult(
        base_url="https://example.com",
        pages=[
            CrawlPage(
                url="https://example.com/",
                path="/",
                title="Example Domain",
                html="<html><body>hello</body></html>",
            )
        ],
    )


@pytest.fixture
def clone_succeeds(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def crawl(self: Any, start_url: str) -> CrawlResult:
        return _one_page()

    async def fake_llm(*_args: Any, **_kwargs: Any) -> Completion:
        return Completion(text="<!DOCTYPE html><html><body>ok</body></html>")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl)
    monkeypatch.setattr(url_to_code, "_generate_with_llm", fake_llm)
    monkeypatch.setattr(url_to_code, "_run_agent_for_page", fake_llm)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")
    return crawl


def test_a_finished_clone_leaves_a_project(
    client: TestClient, signed_in: Any, clone_succeeds: Any
) -> None:
    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        messages = _drain(ws)

    projects = accounts_module.list_projects(signed_in)
    assert len(projects) == 1, f"no project left behind; got {messages[-1:]}"


def test_the_project_is_named_after_the_site(
    client: TestClient, signed_in: Any, clone_succeeds: Any
) -> None:
    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        _drain(ws)

    project = accounts_module.list_projects(signed_in)[0]
    assert project["name"] == "Example Domain"
    assert project["sourceUrl"] == "https://example.com"


def test_the_client_is_told_the_project_count(
    client: TestClient, signed_in: Any, clone_succeeds: Any
) -> None:
    # Otherwise the panel still says 0 of 1 after a clone that worked.
    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://example.com"}))
        messages = _drain(ws)

    set_code = next(m for m in messages if m["type"] == "setCode")
    assert set_code["data"]["usage"]["projects"] == 1


def test_a_failed_clone_leaves_nothing(
    client: TestClient, signed_in: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crawl_failing(self: Any, start_url: str) -> CrawlResult:
        return CrawlResult(base_url=start_url, error="Could not load it.")

    monkeypatch.setattr(url_to_code.SiteCrawler, "crawl", crawl_failing)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "test-key")

    with client.websocket_connect("/url-to-code") as ws:
        ws.send_text(json.dumps({"url": "https://gone.invalid"}))
        _drain(ws)

    assert accounts_module.list_projects(signed_in) == []


class test_when_the_project_list_is_full:
    def test_a_second_clone_still_works_and_says_so(
        self, client: TestClient, signed_in: Any, clone_succeeds: Any
    ) -> None:
        # The free tier keeps one project. A second clone must not be
        # refused after the work is done and paid for: it is kept, and the
        # user is told it was not listed. The daily runs are raised first,
        # or the second clone would be stopped before it began - which is
        # a different question.
        accounts_module.set_limits(signed_in.id, daily_actions=10)

        with client.websocket_connect("/url-to-code") as ws:
            ws.send_text(json.dumps({"url": "https://example.com"}))
            _drain(ws)

        with client.websocket_connect("/url-to-code") as ws:
            ws.send_text(json.dumps({"url": "https://example.org"}))
            messages = _drain(ws)

        completions = [m for m in messages if m["type"] == "variantComplete"]
        assert completions, "the second clone was refused instead of kept"
        # Still one listed, because there is only one slot.
        assert len(accounts_module.list_projects(signed_in)) == 1

    def test_an_administrator_can_make_room(self, signed_in: Any) -> None:
        accounts_module.add_project(signed_in, "old-run", "Old")
        assert len(accounts_module.list_projects(signed_in)) == 1

        accounts_module.set_limits(signed_in.id, max_projects=5)
        accounts_module.note_project(signed_in, "new-run", "New")

        assert len(accounts_module.list_projects(signed_in)) == 2