"""The Telegram bot, and the Mini App it opens.

Two separate jobs live here because they are two separate directions:

* a **webhook**, which Telegram calls to tell us what someone typed. It is
  only listened to when the requests carry the secret token we chose, so
  anyone who guesses the address cannot make this bot say things.
* **sending**, which is this server calling Telegram. The only reason it
  exists is to tell someone their clone has finished, because a Mini App
  that goes quiet the moment someone switches chats looks broken.

Signing a person in from the Mini App happens in routes/accounts.py, not
here. This module never decides who somebody is; it only carries what
telegram_auth proved.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import accounts
import telegram_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

TELEGRAM_API = "https://api.telegram.org"


def bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def webhook_secret() -> str:
    return os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()


def mini_app_url() -> str:
    """Where the bot's button opens.

    Overridable because the answer differs between the machine this was
    written on and the address the bot is actually reachable at, and getting
    it wrong produces a button that opens nothing at all.
    """
    configured = os.environ.get("TELEGRAM_MINI_APP_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return os.environ.get("APP_URL", "").strip().rstrip("/") or "http://localhost:5173"


class MiniAppButton(BaseModel):
    text: str = "Open UrlToCode"
    url: Optional[str] = None


class WebhookUpdate(BaseModel):
    """Only the parts of Telegram's update this bot acts on."""

    update_id: int = 0
    message: Optional[Dict[str, Any]] = None


@router.get("/tariff")
async def tariff() -> Dict[str, Any]:
    """The plans, at what they cost in Stars and what that actually pays.

    Two numbers per plan on purpose. One of them alone is how a $5 tier ends
    up delivering $3.50 and nobody notices until the withdrawal.
    """
    return {"tiers": accounts.tariff(), "commission": accounts.STAR_COMMISSION}


@router.get("/status")
async def status() -> Dict[str, Any]:
    """What is set up here.

    Asked at startup and when something looks wrong. It says whether the
    token is usable and where the button would open, and never prints the
    token itself.
    """
    token = bot_token()
    return {
        "configured": telegram_auth.is_bot_token_usable(token),
        "secretSet": bool(webhook_secret()),
        "miniAppUrl": mini_app_url(),
    }


@router.post("/webhook")
async def webhook(
    request: Request,
    update: WebhookUpdate,
    # Annotated on purpose. A bare scalar parameter here is inferred as a
    # query parameter, so without this the secret arrives as None and every
    # genuine request from Telegram is refused along with the forgeries.
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Where Telegram delivers what someone sent the bot."""
    expected = webhook_secret()
    if not expected:
        raise HTTPException(
            status_code=503, detail="The Telegram webhook is not set up."
        )
    if x_telegram_bot_api_secret_token != expected:
        # The address of a webhook is public. Without this anyone who finds
        # it could post a /start and have the bot talk to the wrong account.
        raise HTTPException(status_code=403, detail="Not from Telegram.")

    message: Dict[str, Any] = update.message or {}
    if str(message.get("text") or "").startswith("/start"):
        return await _handle_start(message)
    return JSONResponse({"ok": True})


async def _handle_start(message: Dict[str, Any]) -> JSONResponse:
    """Answer /start with the button that opens the Mini App.

    The chat id is recorded here, and only for somebody who has already
    opened the app at least once - see accounts.remember_telegram_chat.
    """
    chat: Dict[str, Any] = message.get("chat") or {}
    from_user: Dict[str, Any] = message.get("from") or {}
    chat_id = chat.get("id")
    telegram_user_id = from_user.get("id")

    if isinstance(telegram_user_id, int):
        # Telegram ids are platform-wide, so the same number is both the
        # chat and the user in a private chat.
        accounts.remember_telegram_chat(telegram_user_id, str(chat_id))

    markup: Dict[str, Any] = {
        "inline_keyboard": [
            [{"text": "Open UrlToCode", "web_app": {"url": f"{mini_app_url()}/"}}]
        ],
    }
    await send_message(
        str(chat_id),
        "Paste a link and get a working copy of the site.",
        reply_markup=markup,
    )
    return JSONResponse({"ok": True})


async def send_message(
    chat_id: str,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """Say something in Telegram. False when it could not be sent.

    Never raises: a notification that cannot be delivered is a small
    disappointment, and turning it into an error would fail the clone the
    person was waiting for over a message about it.
    """
    token = bot_token()
    if not telegram_auth.is_bot_token_usable(token) or not chat_id:
        return False
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{TELEGRAM_API}/bot{token}/sendMessage", json=payload)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def announce_ready(account: accounts.Account, project_name: str) -> bool:
    """Tell someone their clone finished.

    Only sent to somebody who has both asked the bot to speak to them and
    has a finished clone - both are checked rather than assumed, because a
    wrong "your site is ready" message to a stranger is worse than none.

    Swallows every failure on purpose. The person this is about is waiting
    on a clone, and a notification that cannot be delivered must not be
    allowed to fail the run it was describing.
    """
    try:
        chat_id = accounts.telegram_chat_for(account)
        if not chat_id:
            return False
        return await send_message(
            chat_id,
            f"{project_name} is ready.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Open it", "web_app": {"url": f"{mini_app_url()}/"}}]
                ]
            },
        )
    except Exception:
        logger.warning("Could not tell Telegram that a clone finished", exc_info=True)
        return False