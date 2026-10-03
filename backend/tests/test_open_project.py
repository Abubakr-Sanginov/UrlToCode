"""Opening a project, and not opening somebody else's.

A run id is a short string that appears in the project list, in
websocket traffic and in URLs. Reading one of these routes with a run id
belonging to another account would hand over their site, so that refusal
is the test that matters here.
"""

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import clone_runs
from clone_runs import ClonePage, CloneRun
from routes import accounts as accounts_route


@pytest.fixture(autouse=True)
def isolated(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(accounts_route.router)
    return TestClient(app)


def sign_in(client: TestClient, email: str) -> Any:
    account = accounts_module.register(email, "correct horse battery")
    import time

    client.cookies.set(
        accounts_route.SESSION_COOKIE, accounts_route._sign(account.id, time.time())
    )
    return account


def a_saved_run(run_id: str) -> None:
    run = CloneRun(run_id=run_id, base_url="https://example.com", stack="html_tailwind")
    run.phase = "done"
    run.portal = "<html><body>portal</body></html>"
    run.page("/").status = clone_runs.COMPLETE
    run.page("/").code = "<html><body>home</body></html>"
    run.generated_files["server/app.py"] = "print('hi')"
    clone_runs.save_run(run)


class test_opening_a_project:
    def test_the_code_comes_back(self, client: TestClient) -> None:
        account = sign_in(client, "mine@test.dev")
        a_saved_run("run-1")
        accounts_module.add_project(account, "run-1", "Example", "https://example.com")

        response = client.get("/api/projects/run-1")

        assert response.status_code == 200
        body = response.json()
        assert body["runId"] == "run-1"
        assert body["baseUrl"] == "https://example.com"
        assert body["code"]["project-structure"]
        assert body["code"]["/"]

    def test_the_generated_files_come_back_too(self, client: TestClient) -> None:
        account = sign_in(client, "mine@test.dev")
        a_saved_run("run-1")
        accounts_module.add_project(account, "run-1", "Example")

        code = client.get("/api/projects/run-1").json()["code"]

        # Kept under the prefix the frontend strips, so a project's server
        # files land in the project rather than being mistaken for a page.
        assert code["file:server/app.py"] == "print('hi')"

    def test_opening_a_project_does_not_spend_a_run(self, client: TestClient) -> None:
        # The whole point of keeping the run is coming back to it without
        # paying to make it again.
        account = sign_in(client, "mine@test.dev")
        a_saved_run("run-1")
        accounts_module.add_project(account, "run-1", "Example")

        client.get("/api/projects/run-1")

        assert accounts_module.usage_of(account, "generate").used == 0


class test_someone_elses_project:
    def test_another_accounts_run_id_is_not_served(self, client: TestClient) -> None:
        theirs = accounts_module.register("theirs@test.dev", "correct horse battery")
        a_saved_run("their-run")
        accounts_module.add_project(theirs, "their-run", "Theirs")
        sign_in(client, "mine@test.dev")

        response = client.get("/api/projects/their-run")

        assert response.status_code == 404
        assert "their-run" not in response.text

    def test_a_run_nobody_claimed_is_not_served(self, client: TestClient) -> None:
        a_saved_run("orphan")
        sign_in(client, "mine@test.dev")

        assert client.get("/api/projects/orphan").status_code == 404

    def test_a_signed_out_caller_gets_nothing(self, client: TestClient) -> None:
        a_saved_run("run-1")

        assert client.get("/api/projects/run-1").status_code in (401, 403)


class test_a_project_whose_files_are_gone:
    def test_it_says_so_rather_than_serving_nothing(self, client: TestClient) -> None:
        # The project is listed but this server no longer holds the run.
        # The user needs to be told that, not shown an empty editor.
        account = sign_in(client, "mine@test.dev")
        accounts_module.add_project(account, "missing-run", "Gone")

        response = client.get("/api/projects/missing-run")

        assert response.status_code == 410

    def test_a_project_with_no_code_yet_says_so(self, client: TestClient) -> None:
        account = sign_in(client, "mine@test.dev")
        empty = CloneRun(
            run_id="empty-run", base_url="https://example.com", stack="html_tailwind"
        )
        clone_runs.save_run(empty)
        accounts_module.add_project(account, "empty-run", "Empty")

        assert client.get("/api/projects/empty-run").status_code == 410
