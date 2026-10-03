"""The session cookie across two addresses.

The site on Vercel and the bot on Railway are two origins. A `SameSite=lax`
cookie is withheld by the browser from every cross-site request, so with one
setting left as it was, every authenticated call fails and the app looks
signed out for reasons nobody can see.

Two tests: one says the split deployment sends a cookie the browser will
actually carry, and one says local development still works, which it cannot
if the cookie is switched on everywhere.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import config
from routes import accounts as accounts_route


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    yield


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(accounts_route.router)
    return TestClient(app)


def sign_in(client: TestClient) -> TestClient:
    """Register and keep the session cookie the response handed back."""
    response = client.post(
        "/api/auth/register",
        json={"email": "someone@test.dev", "password": "correct horse battery"},
    )
    assert response.status_code == 200
    return client


def cookie_header(client: TestClient) -> str:
    """The Set-Cookie from signing in, which is where the policy is set."""
    client.cookies.clear()
    response = client.post(
        "/api/auth/register",
        json={"email": "fresh@test.dev", "password": "correct horse battery"},
    )
    return response.headers.get("set-cookie", "")


class TestOnOneServer:
    def test_the_cookie_travels_normally(self, client: TestClient):
        # Local development, plain http, one address. Lax and not secure is
        # right here and is what the browser expects.
        header = cookie_header(sign_in(client))

        assert "samesite=lax" in header.lower()
        assert "secure" not in header.lower()

    def test_it_really_works(self, client: TestClient):
        signed_in = sign_in(client)

        body = signed_in.get("/api/me").json()

        assert body["account"] is not None


class TestSplitAcrossTwo:
    """Vercel in front, Railway behind."""

    @pytest.fixture(autouse=True)
    def deployed(self, monkeypatch):
        monkeypatch.setattr(
            config,
            "CORS_ALLOWED_ORIGINS",
            ["https://urltocode.vercel.app"],
        )

    def test_the_cookie_is_one_the_browser_will_send_back(self, client: TestClient):
        # Without None and Secure here, every /api call from Vercel arrives
        # signed out and there is nothing in any log to explain it.
        header = cookie_header(sign_in(client))

        assert "samesite=none" in header.lower()
        assert "secure" in header.lower()

    def test_it_stays_httponly(self, client: TestClient):
        # Lifting SameSite is a change to how far a cookie travels, not to
        # what can read it. The page still cannot.
        header = cookie_header(sign_in(client))

        assert "httponly" in header.lower()

    def test_the_session_works_once_the_browser_carries_the_cookie(self, client: TestClient):
        """Given the cookie arriving, the session is a normal one.

        Deliberately not relying on the client to send it by itself: a
        `Secure` cookie is not returned over plain http, and this test suite
        speaks http. That the browser *will* send it is a claim about
        https and about the two assertions above, not something this can
        demonstrate here - so what this proves is that the cookie is not
        broken once it gets across.
        """
        client.cookies.clear()
        response = client.post(
            "/api/auth/register",
            json={"email": "fresh@test.dev", "password": "correct horse battery"},
        )
        token = response.cookies.get(accounts_route.SESSION_COOKIE)
        assert token is not None

        carried = client.get(
            "/api/me",
            headers={"Cookie": f"{accounts_route.SESSION_COOKIE}={token}"},
        )

        assert carried.json()["account"] is not None

    def test_it_is_secure_so_the_browser_will_send_it(self, client: TestClient):
        # The flag is not decoration. Without Secure a None cookie is
        # refused outright, and with Lax this whole arrangement is signed
        # out from the first request.
        header = cookie_header(sign_in(client)).lower()

        assert "samesite=none" in header
        assert "secure" in header


class TestReadingTheSignal:
    def test_https_beside_localhost_counts_as_split(self, monkeypatch):
        """A development machine may have both in its list. One real
        address is enough to decide, and getting it wrong signs everyone
        out."""
        monkeypatch.setattr(
            config,
            "CORS_ALLOWED_ORIGINS",
            ["http://localhost:5173", "https://urltocode.vercel.app"],
        )
        assert accounts_route._cross_site() is True

    def test_only_localhost_is_not_split(self, monkeypatch):
        monkeypatch.setattr(
            config, "CORS_ALLOWED_ORIGINS", ["http://localhost:5173"]
        )
        assert accounts_route._cross_site() is False

    def test_nothing_configured_is_not_split(self, monkeypatch):
        monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", [])
        assert accounts_route._cross_site() is False