"""Signing in with GitHub or Google.

Both providers do the same four things, so this does them once: send the
person to the provider, take a code back, turn the code into a verified
identity, and hand that identity an account here.

Two decisions are worth stating because everything else follows from them.

**The callback lands on the same origin the app is served from.** The
session cookie is set here, and a cookie set by a different origin during a
cross-site redirect is at the mercy of the browser's third-party rules. So
the buttons navigate to /api/auth/<provider>/start on the app's own origin
and everything below is same-origin after that.

**The provider's id is what identifies the person, not their email.** An
email is a thing somebody types and can be changed by anybody with the
account; it is not proof of who is holding it. accounts.account_for_provider
works this through, and it is why the email is passed alongside a flag
saying whether the provider verified it rather than on trust.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Query, Request
from fastapi.responses import RedirectResponse

import accounts
from routes.accounts import SESSION_COOKIE, SESSION_TTL_SECONDS, _issue

router = APIRouter(prefix="/api/auth", tags=["oauth"])

# How long a person may take over the round trip to the provider. Long
# enough to read a password manager's prompt, short enough that a link
# captured from a log is useless by the time anyone finds it.
_STATE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class Provider:
    name: str
    client_id_env: str
    client_secret_env: str
    authorize_url: str
    token_url: str
    scope: str


_PROVIDERS: Dict[str, Provider] = {
    "github": Provider(
        name="github",
        client_id_env="GITHUB_CLIENT_ID",
        client_secret_env="GITHUB_CLIENT_SECRET",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
    ),
    "google": Provider(
        name="google",
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
    ),
}


def available_providers() -> list[str]:
    """Which providers are set up here.

    Asked by the app so a button is only drawn for something that can
    actually work - a dead button on the sign-in screen is worse than no
    button.
    """
    return [
        name
        for name, provider in _PROVIDERS.items()
        if os.environ.get(provider.client_id_env, "").strip()
        and os.environ.get(provider.client_secret_env, "").strip()
    ]


def _state_secret() -> str:
    """A secret of our own for signing the state, so it cannot be forged
    with the session secret alone."""
    configured = os.environ.get("OAUTH_STATE_SECRET", "").strip()
    if configured:
        return configured
    return hashlib.sha256(b"urltocode-oauth-state").hexdigest()


def _sign_state(provider: str, nonce: str, issued_at: float) -> str:
    payload = f"{provider}.{nonce}.{int(issued_at)}"
    digest = hmac.new(
        _state_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{digest}"


def _verify_state(state: str) -> Optional[str]:
    """The provider this state was minted for, if it is ours and fresh.

    Without this the callback accepts any code anyone sends it, which is
    enough to sign a stranger in as whoever they impersonate - so it is
    checked before the code is exchanged, not after.
    """
    try:
        provider, nonce, raw_time, digest = state.split(".")
        issued_at = float(raw_time)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(_sign_state(provider, nonce, issued_at), state):
        return None
    if provider not in _PROVIDERS:
        return None
    if time.time() - issued_at > _STATE_TTL_SECONDS:
        return None
    if time.time() < issued_at - 60:
        return None
    return provider


def _redirect_uri(request: Request, provider: str) -> str:
    """Where the provider should send the person back.

    Taken from the request rather than configured, because the provider
    matches this exactly, character for character, and whoever registered the
    app registered whatever address they were using when they did it. An
    override exists for the case where the app is reached by one address and
    registered under another - a proxy in front, mostly.
    """
    override = os.environ.get("OAUTH_REDIRECT_BASE", "").strip().rstrip("/")
    if override:
        return f"{override}/api/auth/{provider}/callback"
    return str(request.base_url).rstrip("/") + f"/api/auth/{provider}/callback"


def _back_to_app(request: Request) -> str:
    """Where to send the person once there is a session."""
    override = os.environ.get("APP_URL", "").strip().rstrip("/")
    if override:
        return f"{override}/"
    return "/"


def _failure(request: Request, message: str) -> RedirectResponse:
    """Back to the app, saying what went wrong.

    A redirect rather than an error page: the person arrived here from a
    button on the sign-in screen and belongs back on it, not staring at a
    JSON body.
    """
    return RedirectResponse(
        f"{_back_to_app(request)}?{urlencode({'signInError': message})}"
    )


async def _exchange_code(
    provider: Provider, code: str, redirect_uri: str, request: Request
) -> str:
    client_id = os.environ[provider.client_id_env].strip()
    client_secret = os.environ[provider.client_secret_env].strip()
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    # The token is never logged, never echoed, and never reaches the browser.
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            provider.token_url,
            data=payload,
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise _ProviderRefused(f"{provider.name} did not accept the code.")
    body = response.json()
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise _ProviderRefused(f"{provider.name} returned no access token.")
    return token


class _ProviderRefused(Exception):
    """The provider turned us down, or answered with something unusable."""


async def _github_identity(token: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        profile = await client.get("https://api.github.com/user", headers=headers)
        if profile.status_code != 200:
            raise _ProviderRefused("GitHub did not say who you are.")
        user = profile.json()
        emails = await client.get(
            "https://api.github.com/user/emails", headers=headers
        )

    subject = str(user.get("id") or "")
    if not subject:
        raise _ProviderRefused("GitHub did not say who you are.")

    address = ""
    verified = False
    if emails.status_code == 200:
        # Only addresses GitHub has checked. An unverified one is one
        # anybody with the account can type in, so it identifies nothing.
        raw = emails.json()
        entries: List[Dict[str, Any]] = cast(List[Dict[str, Any]], raw) if isinstance(raw, list) else []
        for entry in entries:
            if entry.get("verified"):
                address = str(entry.get("email") or "")
                verified = True
                break
    if not address:
        # A GitHub account with no address at all still gets in, under the
        # id it has, rather than being turned away.
        address = f"{subject}@users.noreply.github.com"
        verified = True
    return {"subject": subject, "email": address, "email_verified": verified}


async def _google_identity(token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise _ProviderRefused("Google did not say who you are.")
    claims = response.json()
    subject = str(claims.get("sub") or "")
    address = str(claims.get("email") or "")
    if not subject or not address:
        raise _ProviderRefused("Google did not say who you are.")
    return {
        "subject": subject,
        "email": address,
        # Google marks an address verified only once it has confirmed it,
        # and says so here. Taken as read for the same reason GitHub's
        # per-address flag is.
        "email_verified": bool(claims.get("email_verified")),
    }


@router.get("/providers")
async def providers() -> Dict[str, Any]:
    return {"providers": available_providers()}


@router.get("/{provider}/start")
async def start(provider: str, request: Request) -> RedirectResponse:
    settings = _PROVIDERS.get(provider)
    if settings is None:
        return _failure(request, "That is not a sign-in option here.")
    if provider not in available_providers():
        return _failure(
            request, f"{provider.title()} sign-in is not set up on this server."
        )

    query = {
        "client_id": os.environ[settings.client_id_env].strip(),
        "redirect_uri": _redirect_uri(request, provider),
        "scope": settings.scope,
        "response_type": "code",
        "state": _sign_state(
            provider, os.urandom(16).hex(), time.time()
        ),
    }
    if provider == "google":
        # Without this Google picks an account by first letter rather than
        # asking, which on a shared machine signs the wrong person in.
        query["prompt"] = "select_account"
    return RedirectResponse(f"{settings.authorize_url}?{urlencode(query)}")


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
) -> RedirectResponse:
    if error:
        # The person pressed Cancel, or the provider refused. Their own
        # doing, not ours to report as a failure.
        return RedirectResponse(_back_to_app(request))

    settings = _PROVIDERS.get(provider)
    if settings is None or provider not in available_providers():
        return _failure(request, "That is not a sign-in option here.")

    if _verify_state(state or "") != provider:
        # Without this the callback accepts any code anyone sends it,
        # which is enough to sign a stranger in as whoever they impersonate.
        # Checked before the code is exchanged, not after.
        return _failure(request, "That sign-in link has expired. Please try again.")
    if not code:
        return _failure(request, "That sign-in did not finish.")

    try:
        token = await _exchange_code(
            settings, code, _redirect_uri(request, provider), request
        )
        identity = (
            await _github_identity(token)
            if provider == "github"
            else await _google_identity(token)
        )
    except _ProviderRefused as exc:
        return _failure(request, str(exc))
    except httpx.HTTPError:
        # Nothing said here about which host or which key: an error page
        # naming them is a page for someone else to read.
        return _failure(request, f"Could not reach {settings.name.title()}.")

    try:
        account = accounts.account_for_provider(
            settings.name,
            identity["subject"],
            identity["email"],
            identity["email_verified"],
        )
    except accounts.AccountError as exc:
        return _failure(request, str(exc))

    response = RedirectResponse(_back_to_app(request))
    _issue(response, account)
    return response