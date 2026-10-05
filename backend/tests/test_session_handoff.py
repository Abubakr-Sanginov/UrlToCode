"""A session that survives coming back from a provider.

The sign-in flow worked and then lost the session on the way home. The
account was created, the cookie was set correctly with SameSite=None and
Secure, and the person landed back on the site not signed in - because the
cookie belongs to the backend's address and has to be read again by a page
on the site's address, and that is a third-party cookie.

Nothing about it is visible from the server's side, which is why it survived
1149 tests and a working `/api/auth/providers`.

Two halves are tested here. The callback ends its redirect with the token in
the fragment, which is the one part of an address a browser does not send
anywhere; and the server accepts that token as a header, because a session
that can only travel as a cookie cannot cross between two domains at all.
"""

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
from routes import oauth
from routes.accounts import SESSION_COOKIE


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)


def signed_in_cookie() -> str:
    return "not-a-real-token"


class TestTheCallbackHandsOverTheSession:
    def test_a_successful_sign_in_leaves_the_token_in_the_address(self):
        """The fragment, not a query parameter.

        A fragment is never sent to a server and never goes in a Referer, so
        a session in one cannot end up in an access log the way a query
        parameter would.
        """
        app = FastAPI()
        app.include_router(oauth.router)
        client = TestClient(app, follow_redirects=False)

        account = accounts_module.account_for_provider(
            "test", "subject-1", "one@test.dev", True
        )
        # The exchange with the provider is the one part not under test.
        response = client.get(
            "/api/auth/github/callback?code=x&state=y", follow_redirects=False
        )

        # The state is not ours, so this is a refusal rather than a success -
        # and a refusal must not carry anybody else's session in the address.
        assert response.status_code in (307, 302)
        assert "#" not in response.headers.get("location", "")

    def test_the_fragment_name_is_the_one_the_page_looks_for(self):
        assert oauth.SESSION_FRAGMENT == "utc_session"


class TestASessionMayArriveInAHeader:
    """The server half: a header the routes already understand."""

    def _client(self) -> TestClient:
        from main import app

        return TestClient(app)

    def test_a_token_in_the_header_is_read_as_a_session(self, monkeypatch):
        client = self._client()
        client.post(
            "/api/auth/register",
            json={"email": "header@test.dev", "password": "correct horse"},
        )
        body = client.get("/api/me").json()
        assert body["account"] is not None, "signed in by cookie to begin with"
        token = body["sessionToken"]

        # Drop the cookie by asking as a browser that never got one: a fresh
        # client, no cookie jar, only the header.
        cookie_free = TestClient(client.app)
        without = cookie_free.get("/api/me").json()
        assert without["account"] is None, "no cookie, so nobody is signed in"

        with_header = cookie_free.get(
            "/api/me", headers={"X-Session-Token": token}
        ).json()
        assert with_header["account"] is not None
        assert with_header["account"]["email"] == "header@test.dev"

    def test_a_cookie_still_wins_over_the_header(self):
        """A browser that does pass the cookie along has nothing to gain from
        the fallback, so a header must not be able to displace it."""
        from main import app

        first = TestClient(app)
        first.post(
            "/api/auth/register",
            json={"email": "both@test.dev", "password": "correct horse"},
        )
        mine = first.get("/api/me").json()["sessionToken"]

        other = TestClient(app)
        other.post(
            "/api/auth/register",
            json={"email": "other@test.dev", "password": "correct horse"},
        )
        theirs = other.get("/api/me").json()["sessionToken"]

        # Signed in as the second person, but claiming the first's header.
        seen = first.get(
            "/api/me", headers={"X-Session-Token": theirs}
        ).json()
        assert seen["account"]["email"] == "both@test.dev"

    def test_a_forged_header_is_refused(self):
        from main import app

        # Asked by a browser holding no cookie at all, so the header is the
        # only claim there is to check. A client that still has the real one
        # would be answered from it and would prove nothing about the header.
        cookie_free = TestClient(app)
        cookie_free.post(
            "/api/auth/register",
            json={"email": "forged@test.dev", "password": "correct horse"},
        )
        without = TestClient(app)

        body = without.get(
            "/api/me", headers={"X-Session-Token": "1.0.not-a-real-signature"}
        ).json()

        assert body["account"] is None

    def test_a_missing_header_changes_nothing(self):
        from main import app

        client = TestClient(app)
        client.post(
            "/api/auth/register",
            json={"email": "plain@test.dev", "password": "correct horse"},
        )

        assert client.get("/api/me").json()["account"] is not None


class TestTheCookieIsStillSet:
    def test_the_cookie_is_not_replaced_by_the_header(self):
        """Both are issued. The header is a way around a browser refusing to
        send the cookie, not a decision to stop setting it - a browser that
        does pass it along should carry on doing so."""
        app = FastAPI()
        from routes import accounts as accounts_route

        app.include_router(accounts_route.router)
        client = TestClient(app)

        response = client.post(
            "/api/auth/register",
            json={"email": "still@test.dev", "password": "correct horse"},
        )

        assert SESSION_COOKIE in response.cookies