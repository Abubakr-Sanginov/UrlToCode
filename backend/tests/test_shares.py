"""Sharing a cloned site: the allowance, the ownership, and what the link serves."""

from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import clone_runs
from clone_runs import COMPLETE, CloneRun, save_run
from routes import accounts as accounts_route
from routes import shares as shares_module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", tmp_path / "clone_runs")
    yield


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(accounts_route.router)
    app.include_router(shares_module.router)
    return TestClient(app)


def sign_in(client: TestClient, email: str = "owner@test.dev") -> str:
    account = accounts_module.register(email, "correct horse battery")
    # Sharing is a paid feature, so the account that signs in to share
    # needs one. Tests that are about the refusal stay on free on purpose.
    accounts_module.set_tier(account.id, "pro")
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "correct horse battery"}
    )
    return response.json()["sessionToken"]


def the_owner(client: TestClient) -> Any:
    """The account this client is signed in as, read back from the session."""
    account_id = accounts_route._verify(client.cookies[accounts_route.SESSION_COOKIE])
    assert account_id is not None
    return accounts_module.account_by_id(account_id)


def a_cloned_site(name: str = "Site", owner: Optional[Any] = None) -> str:
    """A finished clone, and by default a project of the account asking.

    Both are needed: a run that is not a project cannot be shared, because
    the share route checks ownership before it does anything else.
    """
    run_id = f"share-{name.lower()}"
    run = CloneRun(run_id=run_id, base_url="https://example.com", stack="html_tailwind")
    run.phase = "done"
    run.portal = "<html><body>home</body></html>"
    run.page("/").status = COMPLETE
    run.page("/").code = f"<html><body>{name} home</body></html>"
    run.page("/about").status = COMPLETE
    run.page("/about").code = f"<html><body>{name} about</body></html>"
    save_run(run)
    if owner is not None:
        accounts_module.add_project(owner, run_id, name)
    return run_id


class TestTheAllowance:
    def test_a_free_account_cannot_share(self):
        account = accounts_module.register("free@test.dev", "correct horse battery")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")

        with pytest.raises(accounts_module.AccountError) as caught:
            accounts_module.create_share(account, run_id)
        assert "paid plan" in str(caught.value)

    def test_the_starter_plan_makes_one_a_month(self):
        account = accounts_module.register("s@test.dev", "correct horse battery")
        accounts_module.set_tier(account.id, "starter")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")

        first = accounts_module.create_share(account, run_id)

        assert first["limitPerMonth"] == 1
        assert first["remainingThisMonth"] == 0
        with pytest.raises(accounts_module.AccountError):
            accounts_module.create_share(account, run_id)

    def test_the_free_plan_gets_more_than_one_when_raised(self):
        account = accounts_module.register("p@test.dev", "correct horse battery")
        accounts_module.set_tier(account.id, "pro")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")

        made = [accounts_module.create_share(account, run_id) for _ in range(10)]

        assert len(made) == 10
        assert made[-1]["limitPerMonth"] == 10
        with pytest.raises(accounts_module.AccountError):
            accounts_module.create_share(account, run_id)

    def test_links_from_last_month_do_not_count(self, monkeypatch):
        account = accounts_module.register("old@test.dev", "correct horse battery")
        accounts_module.set_tier(account.id, "starter")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")
        accounts_module.create_share(account, run_id)

        # Backdate the one link this month has, so what is left is last
        # month's. A quota that never comes back is not a quota.
        with accounts_module._connect() as conn:
            conn.execute(
                "UPDATE shares SET created_at = ?", (0.0,)
            )

        assert accounts_module.shares_used(account) == 0
        accounts_module.create_share(account, run_id)

    def test_a_revoked_link_frees_nothing(self):
        # The link was made; making it is what the allowance was spent on.
        account = accounts_module.register("r@test.dev", "correct horse battery")
        accounts_module.set_tier(account.id, "starter")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")
        made = accounts_module.create_share(account, run_id)

        accounts_module.revoke_share(account, made["token"])

        assert accounts_module.shares_used(account) == 1


class TestOwnership:
    def test_someone_elses_project_cannot_be_shared(self):
        account = accounts_module.register("a@test.dev", "correct horse battery")
        stranger = accounts_module.register("b@test.dev", "correct horse battery")
        accounts_module.set_tier(account.id, "pro")
        run_id = a_cloned_site()
        accounts_module.add_project(account, run_id, "Site")

        with pytest.raises(accounts_module.AccountError) as caught:
            accounts_module.create_share(stranger, run_id)
        assert "not yours" in str(caught.value)

    def test_only_the_maker_can_revoke_a_link(self):
        maker = accounts_module.register("a@test.dev", "correct horse battery")
        stranger = accounts_module.register("b@test.dev", "correct horse battery")
        accounts_module.set_tier(maker.id, "pro")
        run_id = a_cloned_site()
        accounts_module.add_project(maker, run_id, "Site")
        made = accounts_module.create_share(maker, run_id)

        assert accounts_module.revoke_share(stranger, made["token"]) is False
        assert accounts_module.share_by_token(made["token"]) is not None

    def test_a_token_is_not_guessable(self):
        maker = accounts_module.register("a@test.dev", "correct horse battery")
        accounts_module.set_tier(maker.id, "studio")
        run_id = a_cloned_site()
        accounts_module.add_project(maker, run_id, "Site")

        tokens = {accounts_module.create_share(maker, run_id)["token"] for _ in range(20)}

        # There is no session on a public link, so the token is the only
        # thing standing between one person's site and a stranger's.
        assert len(tokens) == 20
        assert all(len(token) >= 16 for token in tokens)


class TestPublicRoute:
    def test_the_link_serves_the_site(self, client: TestClient):
        sign_in(client)
        run_id = a_cloned_site("Shared", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()

        response = client.get(f"/s/{made['token']}")

        assert response.status_code == 200
        assert "Shared home" in response.text

    def test_it_opens_for_someone_with_no_account(self, client: TestClient):
        sign_in(client)
        run_id = a_cloned_site("Open", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()

        # A different client entirely: no cookie, no session. This is the
        # friend the link was sent to.
        stranger = TestClient(app=client.app)

        response = stranger.get(f"/s/{made['token']}")
        assert response.status_code == 200
        assert "Open home" in response.text

    def test_the_page_shared_is_the_one_asked_for(self, client: TestClient):
        sign_in(client)
        run_id = a_cloned_site("Deep", the_owner(client))
        made = client.post(
            "/api/shares", json={"runId": run_id, "pagePath": "/about"}
        ).json()

        response = client.get(f"/s/{made['token']}")

        assert "Deep about" in response.text

    def test_the_page_is_served_sandboxed(self, client: TestClient):
        """A shared site is markup this server did not write. Served without
        a sandbox it would run on this origin with the reader's session."""
        sign_in(client)
        run_id = a_cloned_site("Owned", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()

        policy = client.get(f"/s/{made['token']}").headers["content-security-policy"]

        assert "sandbox" in policy
        assert "allow-same-origin" not in policy
        # Forms still work: a cloned contact form that silently does nothing
        # is the first thing a friend would notice.
        assert "allow-forms" in policy

    def test_the_page_says_what_it_is(self, client: TestClient):
        # Stops nothing determined, but the link at least does not arrive
        # dressed as something official.
        sign_in(client)
        run_id = a_cloned_site("Owned", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()

        assert "not a page from the site it copies" in client.get(
            f"/s/{made['token']}"
        ).text

    def test_a_revoked_link_stops_working(self, client: TestClient):
        sign_in(client)
        run_id = a_cloned_site("Owned", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()

        client.delete(f"/api/shares/{made['token']}")

        assert client.get(f"/s/{made['token']}").status_code == 404

    def test_a_link_to_a_deleted_clone_says_so(self, client: TestClient):
        sign_in(client)
        run_id = a_cloned_site("Owned", the_owner(client))
        made = client.post("/api/shares", json={"runId": run_id}).json()
        clone_runs.delete_run(run_id)

        assert client.get(f"/s/{made['token']}").status_code in (404, 410)

    def test_an_unknown_token_is_not_found(self, client: TestClient):
        assert client.get("/s/not-a-real-token").status_code == 404


class TestRoutes:
    def test_the_allowance_is_reported_so_the_button_knows(self, client: TestClient):
        sign_in(client)

        body = client.get("/api/shares").json()

        assert body["usage"] == {"used": 0, "limit": 10, "remaining": 10}

    def test_sharing_someone_elses_project_is_refused_by_the_route(self, client: TestClient):
        sign_in(client)
        other = accounts_module.register("other@test.dev", "correct horse battery")
        run_id = a_cloned_site("Theirs", other)

        response = client.post("/api/shares", json={"runId": run_id})

        assert response.status_code == 400
        assert "not yours" in response.json()["detail"]

    def test_listing_links_works_without_a_session(self, client: TestClient):
        assert client.get("/api/shares").status_code == 401