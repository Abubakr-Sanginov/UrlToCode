"""The OAuth routes themselves: what they redirect to, what they refuse.

The provider is never reached from here. A test that hits github.com is a
test that breaks when GitHub is down, needs a network, and can create real
accounts - none of which say anything about whether these routes are right.
"""

from typing import Any, Dict, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
from routes import accounts as accounts_route
from routes import oauth as oauth_module

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("GITHUB_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OAUTH_REDIRECT_BASE", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    yield


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth_module.router)
    # Followed by hand: these routes answer with a redirect on purpose, and
    # a client that followed it would fetch the provider.
    return TestClient(app, follow_redirects=False)


def fake_github(token: str = "gho_token", verified: bool = True) -> Any:
    """Stands in for both calls oauth makes to GitHub."""
    subject = "4242"
    address = "person@example.com"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            assert request.content  # the code was sent
            return httpx.Response(200, json={"access_token": token})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": subject})
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {"email": address, "verified": verified},
                ],
            )
        raise AssertionError(f"unexpected call to {request.url}")

    return handler


@pytest.fixture
def no_network(monkeypatch):
    """Fails any call that is not the one a test swapped in.

    Without this a missing stub silently becomes a live request to GitHub.
    """

    def install(handler: Any) -> None:
        async def guarded(request: httpx.Request) -> httpx.Response:
            result = await handler(request)
            return result

        original = oauth_module.httpx.AsyncClient

        class Client(original):  # type: ignore[misc, valid-type]
            async def __aenter__(self) -> Any:
                await super().__aenter__()
                return self

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                return await guarded(httpx.Request("POST", url, **kwargs))

            async def get(self, url: str, **kwargs: Any) -> httpx.Response:
                return await guarded(httpx.Request("GET", url, **kwargs))

        monkeypatch.setattr(oauth_module.httpx, "AsyncClient", Client)

    return install


def state_from(start: httpx.Response) -> str:
    location = start.headers["location"]
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(location).query)["state"][0]


def error_from(response: httpx.Response) -> str:
    """The message the route sent the person back with.

    Read back through a parse rather than matched against the raw URL: the
    message is encoded on the way out, so comparing against plain text would
    fail on the spaces alone.
    """
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(response.headers["location"]).query)
    return query.get("signInError", [""])[0]


class TestProviderList:
    def test_only_configured_providers_are_offered(self, client: TestClient):
        assert client.get("/api/auth/providers").json() == {"providers": ["github"]}

    def test_a_provider_with_half_a_configuration_is_not_offered(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-only")
        assert client.get("/api/auth/providers").json() == {"providers": ["github"]}


class TestStart:
    def test_it_sends_the_person_to_the_provider(self, client: TestClient):
        response = client.get("/api/auth/github/start")

        assert response.status_code in (302, 307)
        assert response.headers["location"].startswith(
            "https://github.com/login/oauth/authorize"
        )

    def test_it_carries_the_state_the_callback_demands(self, client: TestClient):
        start = client.get("/api/auth/github/start")

        state = state_from(start)
        # Forgeable state is what lets someone sign a stranger in, so this
        # is the whole of the route's security.
        assert oauth_module._verify_state(state) == "github"

    def test_the_callback_url_is_where_the_provider_will_come_back_to(self, client: TestClient):
        start = client.get("/api/auth/github/start")

        from urllib.parse import parse_qs, urlparse

        redirect = parse_qs(urlparse(start.headers["location"]).query)[
            "redirect_uri"
        ][0]
        assert redirect == "http://testserver/api/auth/github/callback"

    def test_an_unknown_provider_is_refused(self, client: TestClient):
        response = client.get("/api/auth/facebook/start")

        assert response.status_code in (302, 307)
        assert "not a sign-in option" in error_from(response)

    def test_a_provider_that_is_not_configured_says_so(self, client: TestClient):
        response = client.get("/api/auth/google/start")

        assert "not set up" in error_from(response)


class TestState:
    def test_a_tampered_state_is_rejected(self):
        state = oauth_module._sign_state("github", "nonce", 1000.0)
        assert oauth_module._verify_state(state.replace("github", "google")) is None

    def test_an_old_state_is_rejected(self):
        state = oauth_module._sign_state("github", "nonce", 0.0)
        assert oauth_module._verify_state(state) is None

    def test_rubbish_is_rejected(self):
        assert oauth_module._verify_state("nonsense") is None


class TestCallback:
    def sign_in_through(self, client: TestClient, state: str, code: str = "the-code") -> httpx.Response:
        return client.get(
            f"/api/auth/github/callback?code={code}&state={state}"
        )

    def test_a_signed_in_person_gets_a_session_and_lands_on_the_app(
        self, client: TestClient, no_network
    ):
        no_network(fake_github())
        state = state_from(client.get("/api/auth/github/start"))

        response = self.sign_in_through(client, state)

        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/"
        assert accounts_route.SESSION_COOKIE in response.cookies

    def test_the_cookie_opens_the_account_the_identity_belongs_to(
        self, client: TestClient, no_network
    ):
        no_network(fake_github())
        state = state_from(client.get("/api/auth/github/start"))
        response = self.sign_in_through(client, state)

        cookie = response.cookies[accounts_route.SESSION_COOKIE]
        account_id = accounts_route._verify(cookie)
        assert account_id is not None
        account = accounts_module.account_by_id(account_id)
        assert account is not None
        assert account.email == "person@example.com"

    def test_a_callback_with_a_forged_state_is_refused(self, client: TestClient, no_network):
        no_network(fake_github())

        response = self.sign_in_through(client, "made.up")

        assert "expired" in error_from(response)
        assert accounts_route.SESSION_COOKIE not in response.cookies

    def test_a_callback_without_a_code_is_refused(self, client: TestClient, no_network):
        no_network(fake_github())
        state = state_from(client.get("/api/auth/github/start"))

        response = client.get(f"/api/auth/github/callback?state={state}")

        assert "did not finish" in error_from(response)
        assert accounts_route.SESSION_COOKIE not in response.cookies

    def test_a_provider_that_refuses_ends_in_a_readable_message(
        self, client: TestClient, no_network
    ):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad_verification_code"})

        no_network(handler)
        state = state_from(client.get("/api/auth/github/start"))

        response = self.sign_in_through(client, state)

        assert "did not accept the code" in error_from(response)
        # And nothing about the secret leaked into a URL.
        assert CLIENT_SECRET not in response.headers["location"]

    def test_pressing_cancel_is_not_reported_as_a_failure(self, client: TestClient, no_network):
        no_network(fake_github())
        state = state_from(client.get("/api/auth/github/start"))

        response = client.get(
            f"/api/auth/github/callback?error=access_denied&state={state}"
        )

        assert response.headers["location"] == "/"
        assert accounts_route.SESSION_COOKIE not in response.cookies

    def test_an_unreachable_provider_says_so_without_naming_anything(
        self, client: TestClient, no_network
    ):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused to github.com")

        no_network(handler)
        state = state_from(client.get("/api/auth/github/start"))

        response = self.sign_in_through(client, state)

        assert "Could not reach" in error_from(response)
        assert CLIENT_SECRET not in response.headers["location"]