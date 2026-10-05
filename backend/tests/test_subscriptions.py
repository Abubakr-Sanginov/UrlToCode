"""Paying monthly rather than once.

The words on the page said "a month" while the invoice was not passing a
period to Telegram at all, so every payment bought a plan for good and then
silently did nothing about the month after it. These tests are about the
three things that have to be true once a payment really is a subscription:

* the invoice asks Telegram for a month, and says so on the payment screen;
* each month is counted once, and a repeated month is not counted twice;
* the plan stops when the month is over, on its own, with nobody asking.

The one worth reading twice is `test_the_same_charge_id_next_month`. Whether
Telegram issues a new charge id for each renewal is not something this code
can assume, so the key is the charge id *and* the cycle. If a later month
turns up carrying the same id and the key were the id alone, the renewal
would be discarded as a duplicate and the customer would pay a month and
lose the plan.
"""

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
from routes import telegram as telegram_module
from tests.test_telegram import BOT_TOKEN, make_init_data

HOOK = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
SECRET = "invoice-signing-secret"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("TELEGRAM_INVOICE_SECRET", SECRET)
    monkeypatch.delenv("TELEGRAM_TEST_STARS", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
    yield


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(telegram_module.router)
    return TestClient(app)


def quiet(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """Record every call out to Telegram instead of making one."""
    calls: List[Any] = []

    async def record(method: str, payload: Dict[str, Any]):
        calls.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(telegram_module, "_telegram_call", record)
    return calls


def payment(
    owner: int,
    tier: str = "pro",
    charge: str = "ch-1",
    expires_at: float = 0.0,
) -> Dict[str, Any]:
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
                **({"subscription_expiration_date": int(expires_at)}
                   if expires_at else {}),
            },
        },
    }


def tier_now(account_id: int) -> str:
    account = accounts_module.account_by_id(account_id)
    assert account is not None
    return accounts_module._current_tier(account)


class TestTheInvoiceAsksForAMonth:
    def test_the_invoice_carries_a_period(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        calls = quiet(monkeypatch)

        response: Any = client.post(
            "/api/telegram/invoice",
            json={"tier": "pro"},
            headers={"X-Telegram-Auth": make_init_data()},
        )

        assert response.status_code == 200
        method, payload = calls[0]
        assert method == "createInvoiceLink"
        # In seconds. Telegram refused 30 and 1 with
        # SUBSCRIPTION_PERIOD_INVALID when this was checked.
        assert payload["subscription_period"] == 2_592_000

    def test_the_payment_screen_says_a_month_not_lifetime(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """The label is what the buyer reads before paying. If it says the
        plan lasts for good while the invoice quietly renews, that is the
        worst way to be wrong about a price."""
        calls = quiet(monkeypatch)

        client.post(
            "/api/telegram/invoice",
            json={"tier": "pro"},
            headers={"X-Telegram-Auth": make_init_data()},
        )

        _, payload = calls[0]
        label = payload["prices"][0]["label"].lower()
        assert "month" in label
        assert "life" not in label
        assert "forever" not in label

    def test_the_invoice_in_the_chat_is_a_month_too(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """There is a second way in: the website's Pay button opens the bot
        with `/start pay_...`, and the bot answers with its own invoice.
        It made a one-off while the website offered a month, so both are
        checked here rather than only the one the tests ran into."""
        calls = quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        # Already carries its own `pay_` prefix; Telegram puts the argument
        # straight after `/start `.
        argument = accounts_module.sign_pay_link(owner.id, "pro", SECRET)

        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "chat": {"id": 1},
                    "from": {"id": 777},
                    "text": f"/start {argument}",
                },
            },
            headers=HOOK,
        )

        invoices = [c for c in calls if c[0] == "sendInvoice"]
        assert len(invoices) == 1, "the bot did not put out an invoice"
        assert invoices[0][1]["subscription_period"] == 2_592_000


class TestCountingEachMonthOnce:
    def test_a_subscription_payment_raises_the_plan_and_dates_it(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        end = time.time() + 30 * 86400

        response: Any = client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=end),
            headers=HOOK,
        )

        assert response.json() == {"ok": True}
        assert tier_now(owner.id) == "pro"

    def test_the_repeated_webhook_for_one_month_is_counted_once(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Telegram retries a webhook until it is answered. One month paid
        once must stay one month."""
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        end = time.time() + 30 * 86400
        body = payment(owner.id, expires_at=end)

        first: Any = client.post(
            "/api/telegram/webhook", json=body, headers=HOOK
        ).json()
        second: Any = client.post(
            "/api/telegram/webhook", json=body, headers=HOOK
        ).json()

        assert first == {"ok": True}
        assert second == {"ok": True}
        assert _row_count("subscription_charges") == 1

    def test_the_same_charge_id_next_month_is_counted_again(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """The case the two tables exist for.

        Whether a renewal carries a fresh charge id is Telegram's business
        and not something this code may assume in either direction. If the
        second month turns up with the same id, keying on the id alone would
        call it a repeat, drop it, and the customer would have paid and lost
        the plan.
        """
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        now = time.time()

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, charge="ch-1", expires_at=now + 30 * 86400),
            headers=HOOK,
        )
        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, charge="ch-1", expires_at=now + 60 * 86400),
            headers=HOOK,
        )

        assert _row_count("subscription_charges") == 2

    def test_a_late_arriving_older_month_does_not_cut_the_newer_one_short(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Renewals can arrive out of order. The end date only ever moves
        forwards, or a late delivery would shorten a month already paid for.
        """
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        now = time.time()

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=now + 60 * 86400),
            headers=HOOK,
        )
        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=now + 30 * 86400),
            headers=HOOK,
        )

        assert _expires_at(owner.id) == pytest.approx(now + 60 * 86400, abs=5)

    def test_a_payment_with_no_period_is_still_a_one_off(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Not every invoice is a subscription. The old behaviour has to
        survive for the ones that are not."""
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook", json=payment(owner.id), headers=HOOK
        )

        assert _row_count("payments") == 1
        assert tier_now(owner.id) == "pro"


class TestThePlanEndingOnItsOwn:
    def test_a_month_that_has_run_out_reads_as_free(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() - 1),
            headers=HOOK,
        )

        assert tier_now(owner.id) == "free"

    def test_the_stale_plan_is_cleared_not_just_ignored(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Left in place it would be read again on every request, and a new
        month starting from a date in the past would end immediately."""
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() - 1),
            headers=HOOK,
        )
        tier_now(owner.id)

        stored = accounts_module.account_by_id(owner.id)
        assert stored is not None
        assert stored.tier == "free"
        assert _expires_at(owner.id) is None

    def test_a_month_that_has_not_run_out_is_still_paid_for(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() + 30 * 86400),
            headers=HOOK,
        )

        assert tier_now(owner.id) == "pro"

    def test_an_account_with_no_date_never_lapses(self, client: TestClient):
        """Every account that existed before this, and every plan an
        administrator granted by hand, has no date on it."""
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        accounts_module.set_tier(owner.id, "studio")

        assert _expires_at(owner.id) is None
        assert tier_now(owner.id) == "studio"


class TestTheOwnerBeingTold:
    def test_a_purchase_reaches_the_owner(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        calls = quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, tier="studio", expires_at=time.time() + 2592000),
            headers=HOOK,
        )

        told = [c for c in calls if c[0] == "sendMessage"]
        assert len(told) == 1
        method, payload = told[0]
        assert payload["chat_id"] == 7_871_227_102
        text = payload["text"]
        assert "Studio" in text
        assert "payer@test.dev" in text
        assert "1080" in text

    def test_the_repeated_webhook_does_not_sell_the_same_month_twice(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """An owner reading a list of purchases cannot tell a repeat from a
        second sale, so the repeat has to be silent."""
        calls = quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        body = payment(owner.id, expires_at=time.time() + 2592000)

        client.post("/api/telegram/webhook", json=body, headers=HOOK)
        client.post("/api/telegram/webhook", json=body, headers=HOOK)

        told = [c for c in calls if c[0] == "sendMessage"]
        assert len(told) == 1

    def test_the_owner_can_be_moved_without_a_deploy(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        calls = quiet(monkeypatch)
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "4242")
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() + 2592000),
            headers=HOOK,
        )

        told = [c for c in calls if c[0] == "sendMessage"]
        assert told[0][1]["chat_id"] == 4242

    def test_an_unusable_owner_id_silences_it_rather_than_guessing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """A typo in the environment must not send a purchase to an
        arbitrary chat."""
        calls = quiet(monkeypatch)
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "not-a-number")
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        response: Any = client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() + 2592000),
            headers=HOOK,
        )

        assert response.json() == {"ok": True}
        assert [c for c in calls if c[0] == "sendMessage"] == []

    def test_the_payment_still_stands_when_the_owner_cannot_be_told(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """The money is already taken and the plan is already raised. A
        receipt that fails to send must not undo either."""
        async def everything_else(method: str, payload: Dict[str, Any]):
            if method == "sendMessage":
                raise RuntimeError("Telegram is down")
            return {"ok": True}

        monkeypatch.setattr(telegram_module, "_telegram_call", everything_else)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")

        response: Any = client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=time.time() + 2592000),
            headers=HOOK,
        )

        assert response.json() == {"ok": True}
        assert tier_now(owner.id) == "pro"


class TestASubscriptionBeingCancelled:
    def test_cancelling_a_renewal_keeps_what_was_already_paid_for(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Cancelling stops the next charge. It does not take back the month
        already paid for, and shortening it would be the wrong thing to do
        on the strength of a cancellation of a charge that was never made."""
        quiet(monkeypatch)
        owner = accounts_module.register("payer@test.dev", "correct horse battery")
        now = time.time()

        client.post(
            "/api/telegram/webhook",
            json=payment(owner.id, expires_at=now + 30 * 86400),
            headers=HOOK,
        )
        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "subscription": {
                    "user": {"id": _telegram_id_for(owner.id)},
                    "expires_at": int(now + 30 * 86400),
                },
            },
            headers=HOOK,
        )

        assert tier_now(owner.id) == "pro"

    def test_a_stranger_cancelling_creates_no_account(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        quiet(monkeypatch)

        response: Any = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3,
                "subscription": {
                    "user": {"id": 999_999_999},
                    "expires_at": int(time.time()),
                },
            },
            headers=HOOK,
        )

        assert response.json() == {"ok": True}
        assert accounts_module.account_for_telegram_id(999_999_999) is None


class TestADatabaseThatWasMadeBeforeThis:
    def test_an_existing_table_gains_the_date_column(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """CREATE TABLE IF NOT EXISTS does nothing for a table that already
        exists, and the deployed database has one. Without this column every
        account reads as having no expiry, so a paid plan would never lapse
        and the migration would look like it had worked."""
        import sqlite3

        path: Path = tmp_path / "old.db"
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
            " tier TEXT NOT NULL DEFAULT 'free', created_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO accounts (email, password_hash, tier, created_at)"
            " VALUES ('old@test.dev', 'x', 'pro', 0)"
        )
        connection.commit()
        connection.close()

        monkeypatch.setattr(accounts_module, "DB_PATH", path)
        monkeypatch.setattr(accounts_module, "_initialised", False)

        def columns_now() -> set:
            connection = sqlite3.connect(path)
            found = {
                row[1] for row in connection.execute("PRAGMA table_info(accounts)")
            }
            tiers = connection.execute("SELECT tier FROM accounts").fetchall()
            connection.close()
            assert [t[0] for t in tiers] == ["pro"]
            return found

        assert "tier_expires_at" not in columns_now()

        accounts_module._ready()

        # The column arrived, and the row that was already there is intact
        # rather than replaced by a rebuilt copy of it.
        assert "tier_expires_at" in columns_now()


def _session_for(account_id: int) -> str:
    from routes import accounts as accounts_route

    return accounts_route._sign(account_id, time.time())


def _telegram_id_for(account_id: int) -> int:
    with accounts_module._connect() as conn:
        row = conn.execute(
            "SELECT telegram_user_id FROM telegram_links WHERE owner_id = ?",
            (account_id,),
        ).fetchone()
    return int(row["telegram_user_id"]) if row else 555_000_000


def _expires_at(account_id: int):
    with accounts_module._connect() as conn:
        row = conn.execute(
            "SELECT tier_expires_at FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return None if row is None else row["tier_expires_at"]


def _row_count(table: str) -> int:
    with accounts_module._connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])