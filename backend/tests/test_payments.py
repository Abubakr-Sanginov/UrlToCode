"""Paying in Stars.

Two things have to hold for a payment to be safe, and neither is obvious:

* a payment arriving must be one this server issued, and
* the same payment arriving twice must raise one plan.

Telegram repeats a webhook until it is answered, and the webhook is the
public address of this app. So every one of these tests is about something
arriving that did not come from here.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import telegram_auth
from routes import accounts as accounts_route
from routes import telegram as telegram_module
from tests.test_telegram import BOT_TOKEN, make_init_data

SECRET = "invoice-signing-secret"
HOOK = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("TELEGRAM_INVOICE_SECRET", SECRET)
    yield


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(telegram_module.router)
    return TestClient(app)


@pytest.fixture
def telegram_ok(monkeypatch):
    """Stands in for the Bot API, recording what would have been sent."""
    sent: Dict[str, Any] = cast(Dict[str, Any], {"calls": cast(List[Any], [])})

    async def fake(method: str, payload: Dict[str, Any]):
        sent["calls"].append((method, payload))
        if method == "createInvoiceLink":
            return cast(Dict[str, Any], {"ok": True, "result": "https://t.me/invoice/pay"})
        return cast(Dict[str, Any], {"ok": True})

    monkeypatch.setattr(telegram_module, "_telegram_call", fake)
    return sent


class TestTheSignedInvoice:
    def test_invoice_payload_fits_in_telegram(self):
        """The ceiling Telegram imposes, which nothing in the code states.

        `payload` over 128 bytes is refused outright with
        INVOICE_PAYLOAD_INVALID and the invoice never reaches the person who
        clicked buy. It cost a live payment to find out: a full hex HMAC
        signature is 64 characters, which put this at 161.

        Checked on the worst case rather than a typical one - the largest
        tier name, the largest owner id, and a hundred fresh payloads, since
        the nonce is random and a single sample could sit a byte under.
        """
        biggest = max(
            len(accounts_module.sign_invoice(999_999_999, tier, SECRET))
            for tier in accounts_module.STAR_PRICES
        )
        for _ in range(100):
            payload = accounts_module.sign_invoice(999_999_999, "studio", SECRET)
            assert len(payload.encode("utf-8")) <= 128, payload

        assert biggest <= 128, f"{biggest} bytes"

    def test_it_says_who_and_what(self):
        payload = accounts_module.sign_invoice(7, "pro", SECRET)

        claim = accounts_module.read_invoice(payload, SECRET)
        assert claim is not None

        assert claim is not None
        assert claim["owner"] == 7
        assert claim["tier"] == "pro"

    def test_a_tampered_payload_is_refused(self):
        payload = accounts_module.sign_invoice(7, "pro", SECRET)

        # Somebody who read this file can sign their own. Somebody who only
        # read the payload cannot: the signature covers the whole body.
        tampered = payload[:-1] + ("0" if payload[-1] != "0" else "1")

        assert accounts_module.read_invoice(tampered, SECRET) is None

    def test_a_payload_signed_with_another_secret_is_refused(self):
        payload = accounts_module.sign_invoice(7, "pro", "a-different-secret")

        assert accounts_module.read_invoice(payload, SECRET) is None

    def test_a_payload_that_is_only_text_is_refused(self):
        assert accounts_module.read_invoice("owner=7,tier=studio", SECRET) is None
        assert accounts_module.read_invoice("", SECRET) is None
        assert accounts_module.read_invoice("abc", SECRET) is None

    def test_an_old_invoice_stops_being_payable(self):
        """A leaked invoice link must not be a way to a free plan months
        later. Signing one does not expire it by itself."""
        old = json.dumps(
            {"owner": 7, "tier": "pro", "issued": int(time.time()) - 7200, "nonce": "x"},
            sort_keys=True,
            separators=(",", ":"),
        )
        signature = hmac.new(SECRET.encode(), old.encode(), hashlib.sha256).hexdigest()
        payload = f"{base64.urlsafe_b64encode(old.encode()).decode()}.{signature}"

        # Correctly signed, and still refused: the age is checked too.
        assert accounts_module.read_invoice(payload, SECRET) is None

    def test_a_plan_that_does_not_exist_is_refused(self):
        payload = accounts_module.sign_invoice(7, "platinum", SECRET)

        assert accounts_module.read_invoice(payload, SECRET) is None


class TestPaying:
    def test_a_signed_in_person_gets_an_invoice(self, client: TestClient, telegram_ok: Dict[str, Any]):
        response = client.post(
            "/api/telegram/invoice",
            json={"tier": "pro"},
            headers={"X-Telegram-Auth": make_init_data()},
        )

        assert response.status_code == 200
        assert response.json()["url"] == "https://t.me/invoice/pay"
        assert response.json()["stars"] == accounts_module.STAR_PRICES["pro"]

    def test_nobody_else_gets_an_invoice(self, client: TestClient, telegram_ok: Dict[str, Any]):
        # Without this, anybody could post {"tier": "studio"} and be sent a
        # link to a payment that upgrades nobody - or, worse, upgrades them.
        forged = make_init_data().replace("777", "888")

        response = client.post(
            "/api/telegram/invoice",
            json={"tier": "studio"},
            headers={"X-Telegram-Auth": forged},
        )

        assert response.status_code == 401
        assert telegram_ok["calls"] == []

    def test_an_unknown_plan_is_refused(self, client: TestClient, telegram_ok: Dict[str, Any]):
        response = client.post(
            "/api/telegram/invoice",
            json={"tier": "platinum"},
            headers={"X-Telegram-Auth": make_init_data()},
        )
        assert response.status_code == 400

    def test_the_invoice_carries_the_price_and_the_payload(self, client: TestClient, telegram_ok: Dict[str, Any]):
        client.post(
            "/api/telegram/invoice",
            json={"tier": "starter"},
            headers={"X-Telegram-Auth": make_init_data()},
        )

        method, payload = telegram_ok["calls"][0]
        assert method == "createInvoiceLink"
        assert payload["currency"] == "XTR"
        assert payload["prices"][0]["amount"] == accounts_module.STAR_PRICES["starter"]
        # The payload is the only thing that comes back identifying what was
        # bought for whom.
        assert accounts_module.read_invoice(payload["payload"], SECRET) is not None

    def test_a_telegram_refusal_is_not_handed_on_as_a_link(self, client: TestClient, monkeypatch):
        async def refused(method: str, payload: Dict[str, Any]):
            # Telegram answers 200 with ok:false for a refused request.
            return None

        monkeypatch.setattr(telegram_module, "_telegram_call", refused)

        response = client.post(
            "/api/telegram/invoice",
            json={"tier": "pro"},
            headers={"X-Telegram-Auth": make_init_data()},
        )

        assert response.status_code == 502


class TestThePaymentLanding:
    def _payment(self, owner: int = 1, tier: str = "pro", charge: str = "ch-1") -> Dict[str, Any]:
        return {
            "update_id": 1,
            "message": {
                "chat": {"id": 1},
                "from": {"id": 777},
                "successful_payment": {
                    "telegram_payment_charge_id": charge,
                    "invoice_payload": accounts_module.sign_invoice(
                        owner, tier, SECRET
                    ),
                    "telegram_payment_amount": 1080,
                    "currency": "XTR",
                },
            },
        }

    def test_a_real_payment_raises_the_plan(self, client: TestClient, monkeypatch):
        async def ignore(method: str, payload: Dict[str, Any]):
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", ignore)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        response = client.post(
            "/api/telegram/webhook", json=self._payment(owner=owner.id), headers=HOOK
        )

        assert response.json() == {"ok": True}
        paid = accounts_module.account_by_id(owner.id)
        assert paid is not None
        assert paid.tier == "pro"

    def test_the_same_payment_twice_raises_one_plan_and_one_row(self, client: TestClient, monkeypatch):
        """Telegram repeats a webhook until it is answered. Charging twice
        for one payment is the kind of bug nobody finds until a refund."""
        async def ignore(method: str, payload: Dict[str, Any]):
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", ignore)
        owner = accounts_module.register("twice@test.dev", "correct horse battery")
        body = self._payment(owner=owner.id)

        client.post("/api/telegram/webhook", json=body, headers=HOOK)
        client.post("/api/telegram/webhook", json=body, headers=HOOK)

        assert len(accounts_module.payments_of(owner)) == 1

    def test_a_forged_payload_grants_nothing(self, client: TestClient, monkeypatch):
        async def ignore(method: str, payload: Dict[str, Any]):
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", ignore)
        owner = accounts_module.register("forger@test.dev", "correct horse battery")
        forged = dict(self._payment(owner=owner.id))
        message = dict(forged["message"])
        payment = dict(message["successful_payment"])
        payment["invoice_payload"] = accounts_module.sign_invoice(
            owner.id, "studio", "a-secret-they-guessed-wrong"
        )
        message["successful_payment"] = payment
        forged["message"] = message

        response = client.post("/api/telegram/webhook", json=forged, headers=HOOK)

        assert response.json()["ok"] is False
        unchanged = accounts_module.account_by_id(owner.id)
        assert unchanged is not None and unchanged.tier == "free"

    def test_a_payment_with_no_charge_id_is_refused(self, client: TestClient, monkeypatch):
        async def ignore(method: str, payload: Dict[str, Any]):
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", ignore)
        owner = accounts_module.register("nocharge@test.dev", "correct horse battery")
        body = self._payment(owner=owner.id)
        payment = dict(body["message"]["successful_payment"])
        del payment["telegram_payment_charge_id"]
        body["message"] = dict(body["message"], successful_payment=payment)

        client.post("/api/telegram/webhook", json=body, headers=HOOK)

        unchanged = accounts_module.account_by_id(owner.id)
        assert unchanged is not None and unchanged.tier == "free"

    def test_a_payment_without_the_hook_secret_changes_nothing(self, client):
        owner = accounts_module.register("nosec@test.dev", "correct horse battery")

        client.post("/api/telegram/webhook", json=self._payment(owner=owner.id))

        unchanged = accounts_module.account_by_id(owner.id)
        assert unchanged is not None and unchanged.tier == "free"


class TestPreCheckout:
    def test_it_answers_about_our_own_invoice(self, client: TestClient, monkeypatch):
        answered: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            answered.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)
        owner = accounts_module.register("asker@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "pre_checkout_query": {
                    "id": "q1",
                    "invoice_payload": accounts_module.sign_invoice(
                        owner.id, "pro", SECRET
                    ),
                },
            },
            headers=HOOK,
        )

        assert answered["pre_checkout_query_id"] == "q1"
        assert answered["ok"] is True

    def test_it_refuses_an_invoice_it_never_issued(self, client: TestClient, monkeypatch):
        answered: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            answered.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "pre_checkout_query": {
                    "id": "q1",
                    "invoice_payload": accounts_module.sign_invoice(
                        1, "studio", "guessed"
                    ),
                },
            },
            headers=HOOK,
        )

        assert answered["ok"] is False

    def test_it_still_answers_when_the_payload_is_rubbish(self, client: TestClient, monkeypatch):
        """Telegram gives ten seconds, then shows the person an error they
        did nothing to cause. Answering is the point, either way."""
        answered: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            answered.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)

        client.post(
            "/api/telegram/webhook",
            json={"update_id": 1, "pre_checkout_query": {"id": "q1"}},
            headers=HOOK,
        )

        assert answered["ok"] is False


class TestDeepLinks:
    def test_a_project_argument_reaches_the_button(self, client: TestClient, monkeypatch):
        sent: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            sent.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "text": "/start project_run-123",
                    "chat": {"id": 1},
                    "from": {"id": 1},
                },
            },
            headers=HOOK,
        )

        button = sent["reply_markup"]["inline_keyboard"][0][0]
        assert button["web_app"]["start_param"] == "project_run-123"

    def test_a_plain_start_carries_nothing(self, client: TestClient, monkeypatch):
        sent: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            sent.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {"text": "/start", "chat": {"id": 1}, "from": {"id": 1}},
            },
            headers=HOOK,
        )

        button = sent["reply_markup"]["inline_keyboard"][0][0]
        assert "start_param" not in button["web_app"]

    def test_a_junk_argument_is_not_passed_on(self, client: TestClient, monkeypatch):
        """This value ends up in the Mini App, where anyone could type
        anything. Forwarding it unfiltered hands a crafted string to the
        server that has to look it up."""
        sent: Dict[str, Any] = {}

        async def fake(method: str, payload: Dict[str, Any]):
            sent.update(payload)
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", fake)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "text": "/start project_../../etc/passwd",
                    "chat": {"id": 1},
                    "from": {"id": 1},
                },
            },
            headers=HOOK,
        )

        button = sent["reply_markup"]["inline_keyboard"][0][0]
        assert "start_param" not in button["web_app"]


class TestTheUpgradeLands:
    def test_a_paid_account_gets_its_allowance(self, client: TestClient, monkeypatch):
        """The payment has to change what the account can actually do, not
        only what the profile page says."""
        async def ignore(method: str, payload: Dict[str, Any]):
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", ignore)
        owner = accounts_module.register("upgraded@test.dev", "correct horse battery")
        before = accounts_module.usage_of(owner)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "chat": {"id": 1},
                    "from": {"id": 777},
                    "successful_payment": {
                        "telegram_payment_charge_id": "ch-up",
                        "invoice_payload": accounts_module.sign_invoice(
                            owner.id, "studio", SECRET
                        ),
                        "telegram_payment_amount": 3240,
                    },
                },
            },
            headers=HOOK,
        )

        paid = accounts_module.account_by_id(owner.id)
        assert paid is not None
        after = accounts_module.usage_of(paid)
        assert after.remaining > before.remaining

class TestPayingFromTheWebsite:
    """The site cannot open a payment sheet - Stars exist only inside
    Telegram - so "Pay" sends the visitor to the bot, and the bot invoices
    the account that asked, whoever is typing in the chat."""

    @pytest.fixture
    def site(self, client: TestClient, monkeypatch) -> TestClient:
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "UrlToCodeBot")
        app = FastAPI()
        app.include_router(telegram_module.router)
        app.include_router(accounts_route.router)
        web = TestClient(app)
        response = web.post(
            "/api/auth/register",
            json={"email": "buyer@example.com", "password": "correct horse battery"},
        )
        assert response.status_code == 200
        return web

    def test_the_link_opens_the_bot_on_the_plan(self, site: TestClient):
        response = site.post("/api/telegram/pay-link", json={"tier": "pro"})

        assert response.status_code == 200
        url = response.json()["url"]
        assert url.startswith("https://t.me/UrlToCodeBot?start=pay_pro-")
        # Telegram refuses a start parameter longer than 64 characters.
        assert len(url.split("start=", 1)[1]) <= 64

    def test_a_signed_out_visitor_gets_no_link(self, client: TestClient):
        response = client.post("/api/telegram/pay-link", json={"tier": "pro"})
        assert response.status_code == 401

    def test_an_unknown_plan_is_refused(self, site: TestClient):
        response = site.post("/api/telegram/pay-link", json={"tier": "platinum"})
        assert response.status_code == 400

    def test_the_bot_invoices_the_account_that_asked(
        self, site: TestClient, telegram_ok: Dict[str, Any]
    ):
        url = site.post("/api/telegram/pay-link", json={"tier": "studio"}).json()["url"]
        start = url.split("start=", 1)[1]
        account_id = site.get("/api/me").json()["account"]["id"]

        # A different person, with no account here, opens the link.
        site.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {"text": f"/start {start}", "chat": {"id": 42}, "from": {"id": 42}},
            },
            headers=HOOK,
        )

        method, payload = telegram_ok["calls"][0]
        assert method == "sendInvoice"
        assert payload["chat_id"] == "42"
        assert payload["currency"] == "XTR"
        assert payload["prices"][0]["amount"] == accounts_module.STAR_PRICES["studio"]
        claim = accounts_module.read_invoice(payload["payload"], SECRET)
        assert claim is not None
        assert claim["owner"] == account_id
        assert claim["tier"] == "studio"

    def test_a_forged_link_gets_no_invoice(
        self, client: TestClient, telegram_ok: Dict[str, Any]
    ):
        forged = f"pay_studio-1-{accounts_module._to_base36(int(time.time()))}-aaaaaaaaaaaaaaaaaaaaaaaa"

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {"text": f"/start {forged}", "chat": {"id": 1}, "from": {"id": 1}},
            },
            headers=HOOK,
        )

        assert all(method != "sendInvoice" for method, _ in telegram_ok["calls"])

    def test_a_link_for_another_plan_does_not_verify(self):
        start = accounts_module.sign_pay_link(5, "starter", SECRET)
        swapped = start.replace("starter", "studio")

        assert accounts_module.read_pay_link(start, SECRET) is not None
        assert accounts_module.read_pay_link(swapped, SECRET) is None

    def test_an_old_link_is_refused(self, monkeypatch):
        start = accounts_module.sign_pay_link(5, "pro", SECRET)
        later = time.time() + accounts_module.INVOICE_TTL_SECONDS + 5
        monkeypatch.setattr(accounts_module.time, "time", lambda: later)

        assert accounts_module.read_pay_link(start, SECRET) is None
