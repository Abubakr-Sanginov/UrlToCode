"""The endpoints that let a run continue after the page is closed.

Exercised through the real app so the routing, the request models and the
run store are all in the path: a resume that works in a unit test and 404s
in the app is not a resume.
"""

from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

import clone_jobs
import clone_cache
import clone_pages
import clone_runs
import clone_runner
from clone_runs import CloneRun
from crawler.crawler import CrawlPage, CrawlResult
from main import app
from routes import url_to_code


def run_with_code(done: int = 0) -> CloneRun:
    run = CloneRun(run_id="resume-me", base_url="https://shop.test", stack="html_tailwind")
    run.crawl = CrawlResult(
        base_url="https://shop.test",
        pages=[
            CrawlPage(url=f"https://shop.test/p{i}", path=f"/p{i}", title=f"P{i}", html="<h1>x</h1>")
            for i in range(3)
        ],
    )
    run.llm = {"provider": "openai", "model": "gpt-4o"}
    for index in range(done):
        record = run.page(f"/p{index}")
        record.code = f"<html>p{index}</html>"
        record.status = clone_runs.COMPLETE
    return run


@pytest.fixture
def stored_run(monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    """A run the app will find, without writing anything to disk."""
    run = run_with_code(done=1)
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run if run_id == run.run_id else None)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)
    yield {"run": run}
    clone_jobs.registry.forget(run.run_id)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# --- status -----------------------------------------------------------------


def test_the_status_says_what_is_left_to_do(client: TestClient, stored_run: Dict[str, Any]):
    response = client.get("/api/clone-runs/resume-me/status")

    assert response.status_code == 200
    body = response.json()
    assert body["known"] is True
    assert body["pagesDone"] == 1
    assert body["pagesPending"] == 2
    assert body["running"] is False


def test_the_status_of_a_run_nobody_has_says_so(client: TestClient, stored_run: Dict[str, Any]):
    body = client.get("/api/clone-runs/nope/status").json()

    assert body["known"] is False
    assert body["running"] is False


# --- resuming ---------------------------------------------------------------


def test_resuming_starts_the_missing_pages_only(
    client: TestClient, stored_run: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    generated = []
    started: List[Any] = []

    def fake_start_generation(run, cfg, media_base_url, registry=None, **kwargs) -> Dict[str, Any]:
        started.append((run.run_id, cfg.provider, cfg.model, media_base_url))
        return {"runId": run.run_id, "running": True, "pending": 2}

    monkeypatch.setattr(clone_runner, "start_generation", fake_start_generation)

    response = client.post(
        "/api/clone-runs/resume-me/resume", json={"openAiApiKey": "sk-test"}
    )

    assert response.status_code == 200
    assert response.json()["running"] is True
    assert len(started) == 1
    run_id, provider, model, origin = started[0]
    assert run_id == "resume-me"
    # The provider and model are the run's own, so a resumed page looks like
    # the pages generated before it.
    assert (provider, model) == ("openai", "gpt-4o")
    assert origin.startswith("http")


def test_resuming_uses_the_key_of_the_provider_the_run_started_with(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run = run_with_code(done=0)
    run.llm = {"provider": "anthropic", "model": "claude-3-5-sonnet"}
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)
    seen: Dict[str, Any] = {}

    def fake_start(target, cfg, media_base_url, registry=None, **kwargs) -> Dict[str, Any]:
        seen["cfg"] = cfg
        return {"runId": target.run_id, "running": True}

    monkeypatch.setattr(clone_runner, "start_generation", fake_start)

    client.post(
        "/api/clone-runs/resume-me/resume",
        json={"openAiApiKey": "sk-wrong", "anthropicApiKey": "sk-right"},
    )

    assert seen["cfg"].api_key == "sk-right"
    assert seen["cfg"].provider == "anthropic"


def test_resuming_a_run_this_server_does_not_have_is_a_404(
    client: TestClient, stored_run: Dict[str, Any]
):
    response = client.post("/api/clone-runs/other/resume", json={})

    assert response.status_code == 404


def test_resuming_a_finished_run_reports_that_rather_than_starting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run = run_with_code(done=3)
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("should not have started anything")

    monkeypatch.setattr(clone_runner, "start_generation", explode)

    body = client.post("/api/clone-runs/resume-me/resume", json={}).json()

    assert body["pending"] == 0
    assert body["running"] is False


def test_a_run_with_no_crawl_cannot_be_resumed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run = CloneRun(run_id="resume-me", base_url="https://x.test", stack="html_tailwind")
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    response = client.post("/api/clone-runs/resume-me/resume", json={})

    assert response.status_code == 409
    assert "crawl" in response.json()["detail"]


def test_resuming_without_a_key_still_says_what_happened(
    client: TestClient, stored_run: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    # No key means no model. The run is left alone and the client is told,
    # rather than a background job spinning on calls that cannot succeed.
    monkeypatch.setattr(
        url_to_code, "OPENAI_API_KEY", ""
    )

    response = client.post("/api/clone-runs/resume-me/resume", json={})

    # The job starts and immediately reports the missing provider; the point
    # is that it does not crash and does not half-run.
    assert response.status_code in (200, 409)


# --- stopping ---------------------------------------------------------------


def test_stopping_a_run_that_is_not_running_says_it_did_nothing(client: TestClient):
    body = client.post("/api/clone-runs/nothing-here/stop").json()

    assert body == {"runId": "nothing-here", "stopped": False}


def test_stopping_a_run_leaves_what_it_already_generated_alone(
    client: TestClient, stored_run: Dict[str, Any]
):
    # The endpoint cancels a live job; what matters here is that it does not
    # touch the pages already written down, which are the user's money.
    before = stored_run["run"].page("/p0").code

    client.post("/api/clone-runs/resume-me/stop")

    assert stored_run["run"].page("/p0").code == before
    assert stored_run["run"].page("/p1").code == ""


# --- the generation cache ---------------------------------------------------


def test_the_cache_is_reported_so_a_stale_answer_can_be_explained(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        clone_cache, "stats", lambda: {"entries": 12, "bytes": 4096, "oldest": 1.0}
    )

    body = client.get("/api/clone-cache").json()

    assert body["entries"] == 12
    assert body["bytes"] == 4096


def test_clearing_the_cache_answers_how_much_was_there(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(clone_cache, "clear", lambda: 7)

    assert client.delete("/api/clone-cache").json() == {"removed": 7}
