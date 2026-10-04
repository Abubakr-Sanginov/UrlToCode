"""Signing in over HTTP, and what an account may then do.

Two things are being pinned here. The first is the limit the user feels:
one project on the free tier, one run a day. The second is the boundary:
a cookie is the only thing standing between one account's projects and
another's, so a cookie that has been edited must not work.
"""

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import accounts as accounts_module
from main import app


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def sign_up(client: TestClient, email: str = "one@test.dev") -> Dict[str, Any]:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


# A provider the route will accept. The repair route checks the allowance
# after it checks that a model is configured, and answers 400 for that, so a
# test about the allowance has to satisfy the other condition first.
#
# Carried in the request body rather than in the environment on purpose: an
# environment key makes the result depend on whose machine ran it. This file
# used to rely on the developer's `.env` having one, which is why it passed in
# isolation and failed in a suite that no longer loaded `.env`.
ANY_USABLE_PROVIDER: Dict[str, Any] = {
    "customProviderBaseUrl": "https://provider.test",
    "customProviderModel": "some-model",
    "customProviderApiKey": "some-key",
}


def test_someone_can_sign_up_and_is_told_their_limits() -> None:
    client = TestClient(app)

    body = sign_up(client)

    assert body["account"]["email"] == "one@test.dev"
    assert body["usage"]["maxProjects"] == 1
    assert body["usage"]["remaining"] == 1


def test_signing_up_twice_with_one_address_is_refused() -> None:
    client = TestClient(app)
    sign_up(client)

    again = client.post(
        "/api/auth/register", json={"email": "one@test.dev", "password": "other pass"}
    )

    assert again.status_code == 400


def test_a_wrong_password_does_not_sign_anyone_in() -> None:
    client = TestClient(app)
    sign_up(client)

    response = client.post(
        "/api/auth/login", json={"email": "one@test.dev", "password": "wrong"}
    )

    assert response.status_code == 401


def test_a_stranger_cannot_learn_who_has_an_account() -> None:
    """The two failures must read the same, or the form lists customers."""
    client = TestClient(app)
    sign_up(client)

    unknown = client.post(
        "/api/auth/login", json={"email": "stranger@test.dev", "password": "correct horse"}
    )
    wrong = client.post(
        "/api/auth/login", json={"email": "one@test.dev", "password": "wrong"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_signed_out_asking_who_i_am_is_not_an_error() -> None:
    client = TestClient(app)

    body = client.get("/api/me").json()

    assert body["account"] is None


def test_signing_in_gives_back_the_session() -> None:
    client = TestClient(app)
    sign_up(client)
    client.post("/auth/logout")

    client.post(
        "/api/auth/login", json={"email": "one@test.dev", "password": "correct horse"}
    )

    assert client.get("/api/me").json()["account"]["email"] == "one@test.dev"


def test_a_edited_cookie_is_not_a_key_to_someone_elses_projects() -> None:
    """Read one account's id out of the cookie and claim to be another.

    Checked before signing up on that client: registering replaces the
    forged cookie with a real one, which would make the check pass for the
    wrong reason.
    """
    owner = TestClient(app)
    sign_up(owner, "mine@test.dev")
    stolen = owner.cookies.get("utc_session")
    assert stolen

    forged = TestClient(app)
    forged.cookies.set("utc_session", stolen.replace("1.", "2.", 1))

    assert forged.get("/api/me").json()["account"] is None
    assert forged.get("/api/projects").status_code == 401


def test_a_cookie_with_a_made_up_signature_is_not_honoured() -> None:
    client = TestClient(app)
    client.cookies.set("utc_session", "1.9999999999.deadbeef")

    assert client.get("/api/me").json()["account"] is None


def test_projects_need_a_sign_in() -> None:
    client = TestClient(app)

    assert client.get("/api/projects").status_code == 401


def test_the_free_tier_stops_at_one_project() -> None:
    client = TestClient(app)
    sign_up(client)

    first = client.post(
        "/api/projects", json={"runId": "run-1", "name": "First", "sourceUrl": "https://x.test"}
    )
    second = client.post(
        "/api/projects", json={"runId": "run-2", "name": "Second", "sourceUrl": "https://y.test"}
    )

    assert first.status_code == 200
    assert second.status_code == 402
    # The refusal has to say what to do, not just that there is a limit.
    assert "project" in str(second.json()["detail"]).lower()


def test_a_saved_project_is_listed_to_its_owner() -> None:
    client = TestClient(app)
    sign_up(client)
    client.post("/api/projects", json={"runId": "run-1", "name": "First", "sourceUrl": "https://x.test"})

    body = client.get("/api/projects").json()

    assert [p["name"] for p in body["projects"]] == ["First"]
    assert body["projects"][0]["sourceUrl"] == "https://x.test"


def test_one_account_cannot_delete_anothers_project() -> None:
    theirs = TestClient(app)
    sign_up(theirs, "theirs@test.dev")
    theirs.post("/api/projects", json={"runId": "run-x", "name": "Theirs", "sourceUrl": ""})

    mine = TestClient(app)
    sign_up(mine, "mine@test.dev")

    assert mine.delete("/api/projects/run-x").json()["projects"] == []
    assert len(theirs.get("/api/projects").json()["projects"]) == 1


def test_a_run_needs_a_sign_in() -> None:
    """Otherwise the daily allowance is only a speed bump."""
    client = TestClient(app)

    assert client.post(
        "/api/clone-runs/run-1/repair", json={}
    ).status_code == 401


def test_the_allowance_is_taken_before_the_work_is_paid_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A limit checked after the model has been paid for is not a limit.

    Stands in for the render so the run reaches the point where the model
    would be asked: what is being pinned is that the refusal comes first,
    not how a page is measured.
    """
    import clone_runs
    import clone_visual
    import routes.url_to_code as url_to_code
    from visual_check import FidelityResult

    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", tmp_path / "runs")

    async def _one_page_below_threshold(*_args: object, **_kwargs: object) -> object:
        return clone_visual.RunCheck(
            run_id="run-1",
            pages=[
                clone_visual.PageCheck(
                    path="/",
                    viewports=[
                        clone_visual.ViewportCheck(
                            name="desktop",
                            width=1366,
                            result=FidelityResult(
                                score=0.1, compared_width=1366, compared_height=768
                            ),
                        )
                    ],
                )
            ],
            skipped=[],
            mean_score=0.1,
            threshold=clone_visual.DEFAULT_REPAIR_THRESHOLD,
        )

    monkeypatch.setattr(url_to_code.clone_visual, "check_run", _one_page_below_threshold)

    client = TestClient(app)
    sign_up(client)
    run = clone_runs.CloneRun(run_id="run-1", base_url="https://x.test", stack="html_tailwind")
    clone_runs.save_run(run, prune=False)
    # The "edit" allowance, which is the one a repair draws on: generating
    # and editing have allowances of their own.
    owner = accounts_module.account_by_id(1)
    assert owner is not None
    accounts_module.spend_action(owner, "edit")

    response = client.post("/api/clone-runs/run-1/repair", json=ANY_USABLE_PROVIDER)

    # Not merely an error: a 402 is the only answer that tells the user
    # they can come back tomorrow.
    assert response.status_code == 402
    detail = str(response.json()["detail"]).lower()
    assert "allowance" in detail or "midnight" in detail