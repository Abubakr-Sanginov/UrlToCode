"""Raising one person's limit, and refusing everyone who is not the operator.

The admin screen is one secret. Everything below is about that secret
holding, and about a raised limit reaching the account it was raised for
rather than sitting in a table.
"""

import pytest
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

import accounts as accounts_module
from routes import admin as admin_module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("ADMIN_TOKEN", "operator-secret")
    yield


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(admin_module.router)
    return TestClient(app)


def an_account(email="a@b.dev"):
    return accounts_module.register(email, "correct horse battery")


def auth(token: str = "operator-secret") -> Dict[str, str]:
    return {"X-Admin-Token": token}


# TestClient's own return types are unknown to the checker, so every call
# that a test keeps a handle on goes through here.
def call(client: TestClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return client.request(method, path, **kwargs)


def body(response: httpx.Response) -> Any:
    return response.json()


class test_the_token_holds:
    def test_the_right_token_lists_accounts(self, client):
        an_account()
        response = call(client, "GET", "/api/admin/accounts", headers=auth())
        assert response.status_code == 200
        assert body(response)["accounts"][0]["email"] == "a@b.dev"

    def test_the_wrong_token_lists_nothing(self, client):
        an_account()
        response = call(client, "GET", "/api/admin/accounts", headers=auth("guess"))
        assert response.status_code == 401

    def test_no_token_at_all_is_refused(self, client):
        response = call(client, "GET", "/api/admin/accounts")
        assert response.status_code == 401

    def test_an_unconfigured_admin_token_opens_nothing(self, client, monkeypatch):
        # Blank does not mean "no password". An unset token must fail
        # closed, or sending nothing would be enough to get in.
        monkeypatch.setenv("ADMIN_TOKEN", "   ")
        response = call(client, "GET", "/api/admin/accounts", headers=auth(""))
        assert response.status_code == 503

    def test_the_wrong_token_cannot_raise_a_limit(self, client):
        account = an_account()
        response = call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth("guess"), json={"dailyActions": 99})
        assert response.status_code == 401
        assert accounts_module.usage_of(account, "generate").remaining == 1


class test_raising_a_limit:
    def test_a_raised_allowance_takes_effect_at_once(self, client):
        # Not on the next sign-in. The limit is read from the database on
        # every check, so the person's next click already sees it.
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 10})

        for attempt in range(10):
            accounts_module.spend_action(account, "generate")

        assert accounts_module.usage_of(account, "generate").remaining == 0

    def test_the_raised_allowance_is_what_the_account_is_told(self, client):
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 5})

        assert accounts_module.usage_of(account, "generate").remaining == 5

    def test_one_person_does_not_move_anyone_else(self, client):
        mine = an_account("mine@b.dev")
        theirs = an_account("theirs@b.dev")
        client.post(
            f"/api/admin/accounts/{mine.id}/limits",
            headers=auth(),
            json={"dailyActions": 50},
        )

        assert accounts_module.usage_of(theirs, "generate").remaining == 1

    def test_a_project_limit_can_be_raised_on_its_own(self, client):
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"maxProjects": 25})

        assert accounts_module.usage_of(account).max_projects == 25
        # The other limit still follows the tier.
        assert accounts_module.usage_of(account).remaining == 1

    def test_clearing_sends_the_account_back_to_its_tier(self, client):
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 50})

        client.delete(f"/api/admin/accounts/{account.id}/limits", headers=auth())

        assert accounts_module.usage_of(account, "generate").remaining == 1

    def test_a_note_is_kept_for_the_next_person_who_looks(self, client):
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 5, "note": "paid, invoiced 12 May"})

        listed: Dict[str, Any] = body(call(client, "GET", "/api/admin/accounts", headers=auth()))
        assert listed["accounts"][0]["note"] == "paid, invoiced 12 May"

    def test_zero_is_an_answer_and_not_a_missing_one(self, client):
        # Someone can be stopped deliberately. That is different from
        # being left unset, and the two must not look alike.
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 0})

        assert accounts_module.usage_of(account, "generate").remaining == 0
        with pytest.raises(accounts_module.NoCapacity):
            accounts_module.spend_action(account, "generate")

    def test_an_unknown_account_is_reported(self, client):
        response = call(client, "POST", "/api/admin/accounts/9999/limits", headers=auth(), json={"dailyActions": 5})
        assert response.status_code == 400


class test_the_summary:
    def test_it_shows_what_today_cost(self, client):
        account = an_account()
        accounts_module.spend_action(account, "generate")
        accounts_module.spend_action(account, "edit")

        summary = accounts_module.account_summary(account.id)
        assert summary["usedGenerate"] == 1
        assert summary["usedEdit"] == 1

    def test_it_shows_the_tier_underneath_an_override(self, client):
        # The screen has to be able to say both: what this person has now,
        # and what their plan would give them without an override.
        account = an_account()
        call(client, "POST", f"/api/admin/accounts/{account.id}/limits", headers=auth(), json={"dailyActions": 42})

        summary = accounts_module.account_summary(account.id)
        assert summary["dailyActions"] == 42
        assert summary["overrideDailyActions"] == 42

    def test_a_tier_with_no_override_reports_none(self, client):
        account = an_account()

        assert accounts_module.account_summary(account.id)["overrideDailyActions"] is None
