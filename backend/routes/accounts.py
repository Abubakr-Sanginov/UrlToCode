"""Signing in, and finding out what an account may still do.

The session is a cookie holding an account id. It is signed, so a
stranger cannot read one account's id out of the cookie and walk into
another's projects by editing a number - which is the whole of what stands
between "knows an id" and "owns an account".

These routes do not decide what a free account gets; accounts.py does. Here
we only turn its answers into HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

import accounts
import clone_runs
import telegram_auth

router = APIRouter(prefix="/api", tags=["accounts"])

SESSION_COOKIE = "utc_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30

# A secret per deployment. A fixed value in the source would mean anyone
# with the code can mint a cookie for any account id.
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip() or hashlib.sha256(
    b"urltocode-dev-secret-change-me"
).hexdigest()


def _sign(account_id: int, issued_at: float) -> str:
    payload = f"{account_id}.{int(issued_at)}"
    digest = hmac.new(
        _SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{digest}"


def _verify(token: str) -> Optional[int]:
    """The account id in this cookie, if it is one we issued and it is fresh."""
    try:
        raw_id, raw_time, digest = token.split(".")
        issued_at = float(raw_time)
        account_id = int(raw_id)
    except (ValueError, AttributeError):
        return None
    expected = _sign(account_id, issued_at)
    if not hmac.compare_digest(expected, token):
        return None
    if time.time() - issued_at > SESSION_TTL_SECONDS:
        return None
    return account_id


def current_account(session: Optional[str]) -> Optional[accounts.Account]:
    """Who is asking, if anyone."""
    if not session:
        return None
    account_id = _verify(session)
    if account_id is None:
        return None
    return accounts.account_by_id(account_id)


def require_account(session: Optional[str]) -> accounts.Account:
    account = current_account(session)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return account


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class SaveProjectRequest(BaseModel):
    runId: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    sourceUrl: str = Field(default="", max_length=2000)


def _usage_payload(usage: accounts.Usage) -> Dict[str, Any]:
    return {
        "used": usage.used,
        "remaining": usage.remaining,
        "projects": usage.projects,
        "maxProjects": usage.max_projects,
        "tier": usage.tier,
        "resetsAt": usage.resets_at,
    }


def _issue(response: Response, account: accounts.Account) -> str:
    """Set the session cookie and hand the token back to the page.

    The token comes back in the body as well because a browser cannot put
    a cookie on a websocket handshake to another origin, and the run itself
    is a websocket. The cookie is HttpOnly and stays that way - it is not
    loosened so the page can read it.
    """
    token = _sign(account.id, time.time())
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return token


@router.post("/auth/register")
async def register(body: Credentials, response: Response) -> Dict[str, Any]:
    try:
        account = accounts.register(body.email, body.password)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token = _issue(response, account)
    return {
        "account": {"id": account.id, "email": account.email},
        "usage": _usage_payload(accounts.usage_of(account)),
        "sessionToken": token,
    }


@router.post("/auth/login")
async def login(body: Credentials, response: Response) -> Dict[str, Any]:
    """One answer for a wrong password and an unknown address alike."""
    try:
        account = accounts.authenticate(body.email, body.password)
    except accounts.AccountError:
        account = None
    if account is None:
        raise HTTPException(
            status_code=401, detail="That email and password do not match."
        )

    token = _issue(response, account)
    return {
        "account": {"id": account.id, "email": account.email},
        "usage": _usage_payload(accounts.usage_of(account)),
        "sessionToken": token,
    }


@router.post("/auth/logout")
async def logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


class TelegramSignIn(BaseModel):
    """What the Mini App sends to be signed in.

    `initData` is what Telegram signed into the page, and it is the whole
    of the request as far as identity is concerned.
    """

    initData: str = Field(default="")
    chatId: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)


@router.get("/me")
async def me(
    response: Response,
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Who is asking, and what is left. Never an error when signed out."""
    account = current_account(utc_session)
    if account is None:
        return {"account": None}
    return {
        "account": {"id": account.id, "email": account.email},
        "usage": _usage_payload(accounts.usage_of(account)),
        # Handed back on every page load so a reload can restore the token
        # the websocket needs. The cookie alone cannot do that: it is
        # HttpOnly, so the page cannot read it.
        "sessionToken": _sign(account.id, time.time()),
    }


@router.post("/auth/telegram")
async def telegram_sign_in(
    response: Response,
    body: TelegramSignIn,
) -> Dict[str, Any]:
    """Sign in from inside Telegram, with no password anywhere.

    The client sends what Telegram signed into the page. That is checked
    here, on the server, against the bot token - a Mini App is an ordinary
    web page and could claim to be anybody. An unverified blob is refused
    exactly as a wrong password is.

    The blob is the only thing that decides who this is. The session cookie
    is deliberately not consulted, even though it would arrive on the
    request by itself: on a shared device it belongs to whoever used it
    last, and trusting it here would hand that person's projects - their
    clones, their paid links - to the next person to open Telegram.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    user = telegram_auth.verify(body.initData, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="That is not Telegram.")

    try:
        account = accounts.account_for_telegram(
            user.id, user.username, body.email or ""
        )
    except accounts.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.chatId:
        # Only recorded once the account is settled, so a chat id cannot
        # attach a stranger's conversation to somebody's account.
        accounts.remember_telegram_chat(user.id, body.chatId)

    return {
        "account": {"id": account.id, "email": account.email},
        "usage": _usage_payload(accounts.usage_of(account)),
        "sessionToken": _issue(response, account),
    }


@router.get("/projects")
async def list_projects(
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    account = require_account(utc_session)
    return {
        "projects": accounts.list_projects(account),
        "usage": _usage_payload(accounts.usage_of(account)),
    }


@router.post("/projects")
async def save_project(
    body: SaveProjectRequest,
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Claim a saved clone as one of this account's projects."""
    account = require_account(utc_session)
    try:
        usage = accounts.add_project(account, body.runId, body.name, body.sourceUrl)
    except accounts.NoCapacity as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    return {
        "projects": accounts.list_projects(account),
        "usage": _usage_payload(usage),
    }


@router.get("/projects/{run_id}")
async def open_project(
    run_id: str,
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """The code behind one of this account's projects, ready to open.

    Ownership is checked before the run is even read: a project list is a
    list of run ids, and anyone who has ever seen one could otherwise ask
    for another's site by changing a number.
    """
    account = require_account(utc_session)
    if not accounts.owns_project(account, run_id):
        raise HTTPException(status_code=404, detail="No such project.")

    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=410,
            detail="That project is listed but its files are no longer on this"
            " server.",
        )
    code = run.code_map()
    if not code:
        raise HTTPException(
            status_code=410, detail="That project has no generated code yet."
        )
    return {
        "runId": run_id,
        "baseUrl": run.base_url,
        "stack": run.stack,
        "phase": run.phase,
        "code": code,
    }


@router.delete("/projects/{run_id}")
async def delete_project(
    run_id: str,
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    account = require_account(utc_session)
    accounts.remove_project(account, run_id)
    return {
        "projects": accounts.list_projects(account),
        "usage": _usage_payload(accounts.usage_of(account)),
    }