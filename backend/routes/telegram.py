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
from typing import Any, Dict, List, Optional, cast

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import accounts
import telegram_auth
from routes.accounts import require_account
from telegram_auth import TelegramUser

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


_bot_username_cache: Optional[str] = None


async def bot_username() -> Optional[str]:
    """The bot's @name, without the @, for building t.me links.

    Configured when it is known, asked of Telegram when it is not, and then
    remembered: it never changes for a given token.
    """
    global _bot_username_cache
    configured = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if configured:
        return configured
    if _bot_username_cache:
        return _bot_username_cache
    answer = await _telegram_call("getMe", {})
    result = cast(Dict[str, Any], (answer or {}).get("result") or {})
    name = str(result.get("username") or "")
    if name:
        _bot_username_cache = name
        return name
    return None


class MiniAppButton(BaseModel):
    text: str = "Open UrlToCode"
    url: Optional[str] = None


class InvoiceRequest(BaseModel):
    tier: str


class WebhookUpdate(BaseModel):
    """Only the parts of Telegram's update this bot acts on."""

    update_id: int = 0
    message: Optional[Dict[str, Any]] = None
    pre_checkout_query: Optional[Dict[str, Any]] = None


def invoice_secret() -> str:
    """What invoices are signed with.

    The bot token by default: it is already a secret this server holds and
    nothing else does, and rotating it should invalidate invoices that were
    never paid for. Overridable where the bot token is shared with something
    that does not need it.
    """
    return os.environ.get("TELEGRAM_INVOICE_SECRET", "").strip() or bot_token()


def require_mini_app_user(
    x_telegram_auth: Optional[str] = Header(default=None),
) -> TelegramUser:
    """The Telegram user behind a request, or a refusal.

    The signed blob goes in the header rather than the body, so that a link,
    a form, or anything else that can put something into a URL cannot
    substitute one.

    A dependency rather than a check inside each handler, because the check
    is the whole security of the endpoint and the failure mode of forgetting
    it is handing somebody an invoice for the plan they asked for.
    """
    token = bot_token()
    user = telegram_auth.verify(x_telegram_auth or "", token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="That is not Telegram.")
    return user


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

    # Every one of these arrives over an authenticated webhook, and none is
    # trusted further than that: each re-checks its own payload.
    if update.pre_checkout_query is not None:
        return await _handle_pre_checkout(update.pre_checkout_query)

    message: Dict[str, Any] = update.message or {}
    payment = message.get("successful_payment")
    if isinstance(payment, dict):
        return await _handle_successful_payment(payment)

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

    # `/start pay_<tier>-...` comes from the website's "Pay" button: the bot
    # answers with the invoice itself rather than the Mini App button.
    pay = _pay_from_command(message.get("text"))
    if pay is not None:
        return await _send_plan_invoice(str(chat_id), pay)

    # `/start project_abc123` puts that in the button, so the Mini App opens
    # on that project instead of at its front door. Telegram caps this at 64
    # characters, which is why the run id is carried rather than a title.
    start_param = _project_from_command(message.get("text"))

    button: Dict[str, Any] = {"text": "Open UrlToCode", "web_app": {"url": f"{mini_app_url()}/"}}
    if start_param:
        button["web_app"]["start_param"] = start_param

    markup: Dict[str, Any] = {"inline_keyboard": [[button]]}
    await send_message(
        str(chat_id),
        "Paste a link and get a working copy of the site.",
        reply_markup=markup,
    )
    return JSONResponse({"ok": True})


def _pay_from_command(text: Any) -> Optional[Dict[str, Any]]:
    """The verified plan request in `/start pay_...`, if there is one."""
    parts = str(text or "").split()
    if len(parts) < 2 or not parts[1].startswith("pay_"):
        return None
    return accounts.read_pay_link(parts[1], invoice_secret())


async def _send_plan_invoice(chat_id: str, claim: Dict[str, Any]) -> JSONResponse:
    """Put the Stars invoice for a plan into this chat.

    The invoice is signed for the account that asked on the website, not for
    whoever is typing in the chat. Paying for somebody else's plan is
    harmless; crediting the wrong account is not, and the website's account
    is the one that knew what it wanted.
    """
    tier = str(claim["tier"])
    stars = accounts.chargeable_stars(tier)
    if accounts.test_stars() is not None:
        logger.warning(
            "Charging %d Stars for %s: TEST PRICING IS ON.", stars, tier
        )
    answer = await _telegram_call(
        "sendInvoice",
        {
            "chat_id": chat_id,
            "title": f"UrlToCode {tier.title()}",
            # At test prices the dollars are not what is being charged, and
            # saying otherwise on the payment screen is a lie somebody has to
            # act on.
            "description": (
                f"{stars} Stars - test price."
                if accounts.test_stars() is not None
                else f"{accounts.TIER_DOLLARS[tier]} dollars of UrlToCode a month."
            ),
            "payload": accounts.sign_invoice(int(claim["owner"]), tier, invoice_secret()),
            "currency": "XTR",
            "prices": [{"label": f"{tier.title()} plan", "amount": stars}],
        },
    )
    if answer is None:
        await send_message(
            chat_id, "I could not make out the invoice just now. Please try the button again."
        )
    return JSONResponse({"ok": True})


def _project_from_command(text: Any) -> Optional[str]:
    """The project id in `/start project_<id>`, if that is what was typed.

    Only the run id is carried, and it is checked as one: this value ends
    up in the Mini App, where somebody could type anything at all. Passing it
    on unfiltered would let a crafted link put a strange value in front of
    the server that has to look it up.
    """
    parts = str(text or "").split()
    if len(parts) < 2:
        return None
    command, _, argument = parts[1].partition("_")
    if command != "project" or not argument:
        return None
    if len(argument) > 40 or not all(c.isalnum() or c in "-_" for c in argument):
        return None
    # The prefix goes back on. One parameter will carry several kinds of
    # deep link, and the app needs to be able to tell them apart.
    return f"project_{argument}"


@router.get("/payments")
async def list_payments(
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """What this account has paid for.

    On the session cookie, not the Mini App blob: this is a reading, and the
    cookie is what a page load already carries.
    """
    account = require_account(utc_session)
    return {"payments": accounts.payments_of(account)}


async def _telegram_call(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One call to the Bot API. The answer, or None if it could not be made.

    A single place for the token, the address and the network, so every
    caller handles failure the same way instead of deciding for itself.

    A refusal is logged with Telegram's own words. Telegram always says why
    — "bot can't initiate conversation with a user", "chat not found" — and
    throwing that away turns a one-line answer into a guessing game: the
    caller only sees that something failed, and every failure looks the same
    from the outside.
    """
    token = bot_token()
    if not telegram_auth.is_bot_token_usable(token):
        logger.warning("Telegram call %s refused: no usable bot token here.", method)
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Telegram call %s could not be made: %s", method, exc)
        return None
    if response.status_code != 200:
        logger.warning(
            "Telegram call %s answered HTTP %s: %s",
            method,
            response.status_code,
            response.text[:200],
        )
        return None
    try:
        answer = response.json()
    except ValueError:
        logger.warning("Telegram call %s answered with something that is not JSON.", method)
        return None
    # Telegram answers 200 with ok:false for a refused request, and treating
    # that as success is how a refused invoice becomes a link that opens an
    # error page.
    if not answer.get("ok"):
        logger.warning("Telegram refused %s: %s", method, answer.get("description"))
        return None
    return answer


@router.post("/pay-link")
async def create_pay_link(
    body: InvoiceRequest,
    utc_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """A link that opens the bot on one of the paid plans.

    For the website, where there is no Telegram to ask for a payment sheet.
    The account is the signed-in session's, never a field of the request.
    """
    account = require_account(utc_session)
    if body.tier not in accounts.STAR_PRICES:
        raise HTTPException(status_code=400, detail="There is no such plan.")

    name = await bot_username()
    if not name:
        raise HTTPException(status_code=503, detail="The Telegram bot is not set up.")

    start = accounts.sign_pay_link(account.id, body.tier, invoice_secret())
    return {
        "url": f"https://t.me/{name}?start={start}",
        "tier": body.tier,
        "stars": accounts.chargeable_stars(body.tier),
    }


@router.post("/invoice")
async def create_invoice(
    body: InvoiceRequest,
    user: TelegramUser = Depends(require_mini_app_user),
) -> Dict[str, Any]:
    """A payment link for one of the paid plans.

    The Mini App asks for this after Telegram has signed the person in, so
    the account is known here rather than taken from the request. An
    unsigned "owner_id: 1, tier: studio" is exactly what this refuses to
    take.
    """
    tier = body.tier
    if tier not in accounts.STAR_PRICES:
        raise HTTPException(status_code=400, detail="There is no such plan.")

    account = accounts.account_for_telegram(user.id, user.username)
    payload = accounts.sign_invoice(account.id, tier, invoice_secret())
    stars = accounts.chargeable_stars(tier)
    if accounts.test_stars() is not None:
        logger.warning(
            "Charging %d Stars for %s: TEST PRICING IS ON.", stars, tier
        )

    answer = await _telegram_call(
        "createInvoiceLink",
        {
            "title": f"UrlToCode {tier.title()}",
            "description": (
                f"{stars} Stars - test price."
                if accounts.test_stars() is not None
                else f"{accounts.TIER_DOLLARS[tier]} dollars of UrlToCode a month."
            ),
            "payload": payload,
            "currency": "XTR",
            "prices": [
                {"label": f"{tier.title()} plan", "amount": stars}
            ],
        },
    )
    if answer is None:
        raise HTTPException(
            status_code=502, detail="Telegram would not make out an invoice."
        )
    return {
        "url": answer.get("result"),
        "tier": tier,
        "stars": stars,
        "dollars": accounts.TIER_DOLLARS[tier],
        "net": accounts.stars_net(stars),
    }


async def _handle_pre_checkout(query: Dict[str, Any]) -> JSONResponse:
    """Telegram asking whether this payment may go ahead.

    Answered within ten seconds or the payment fails and the person is shown
    an error they did nothing to cause. So this does one thing: check the
    payload we issued, and answer.
    """
    query_id = str(query.get("id") or "")
    claim = accounts.read_invoice(
        str(query.get("invoice_payload") or ""), invoice_secret()
    )
    await _telegram_call(
        "answerPreCheckoutQuery",
        {"pre_checkout_query_id": query_id, "ok": claim is not None},
    )
    return JSONResponse({"ok": True})


async def _handle_successful_payment(payment: Dict[str, Any]) -> JSONResponse:
    """The money has moved. Raise the plan.

    The charge id is Telegram's own and is recorded as the primary key, so
    a webhook arriving twice raises one plan rather than two and one row.
    """
    claim = accounts.read_invoice(
        str(payment.get("invoice_payload") or ""), invoice_secret()
    )
    charge_id = str(payment.get("telegram_payment_charge_id") or "")
    if claim is None or not charge_id:
        # Refused rather than acknowledged. Telegram retries a webhook that
        # is not answered, which would keep asking about a payment whose
        # payload never came from us.
        return JSONResponse({"ok": False, "reason": "unverified payload"})

    stars = int(payment.get("telegram_payment_amount") or 0)
    recorded = accounts.record_payment(
        charge_id, int(claim["owner"]), str(claim["tier"]), stars
    )
    logger.info(
        "Stars payment recorded: tier=%s stars=%s new=%s",
        claim["tier"], stars, recorded,
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
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await _telegram_call("sendMessage", payload) is not None


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