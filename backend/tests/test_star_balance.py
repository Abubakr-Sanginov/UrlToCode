"""The owner's Stars balance.

Two things are being kept true, and only one of them is arithmetic:

* nobody who is not the bot's owner is told the balance, and
* the number is not invented.

Telegram's Bot API has no "what is my balance" method at all - the money is
withdrawn in Fragment and nothing in the API will move it. So the balance
here is worked out from the transaction list, which is the same money counted
from the other side. An earlier version of this endpoint returned a made-up
"withdrawable" figure, and a number that looks like money in hand is worse
than no number at all.
"""

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
from routes import telegram as telegram_module
from tests.test_telegram import BOT_TOKEN, make_init_data

OWNER = 7_871_227_102
STRANGER = 555_555_555


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
    yield


@pytest.fixture
def app() -> FastAPI:
    built = FastAPI()
    built.include_router(telegram_module.router)
    return built


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def telegram_answers(
    monkeypatch: pytest.MonkeyPatch, transactions: List[Dict[str, Any]]
) -> List[Any]:
    calls: List[Any] = []

    async def record(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append((method, payload))
        return {"ok": True, "result": {"transactions": transactions}}

    monkeypatch.setattr(telegram_module, "_telegram_call", record)
    return calls


def as_user(app: FastAPI, telegram_id: int) -> None:
    """Stand in for the Telegram id Telegram signed.

    Through dependency_overrides, not by patching the module. Depends()
    captured the function when the route was declared, so replacing the
    attribute afterwards changes nothing - which is a test that passes for
    the wrong reason.
    """
    app.dependency_overrides[telegram_module.require_mini_app_user] = (
        lambda: _user(telegram_id)
    )


class TestWhoIsAllowedToAsk:
    def test_the_owner_is_told_the_balance(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        telegram_answers(monkeypatch, [{"amount": 360}, {"amount": 1080}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        response = client.get("/api/telegram/balance")

        assert response.status_code == 200
        assert response.json()["balance"] == 1440

    def test_anybody_else_is_refused(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """The check is on the Telegram id the server verified, not on
        anything the page claims. A button that is merely not drawn is not a
        thing anybody can be stopped from pressing."""
        telegram_answers(monkeypatch, [{"amount": 360}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, STRANGER)

        response = client.get("/api/telegram/balance")

        assert response.status_code == 403
        assert "balance" not in response.json()

    def test_the_refused_answer_leaks_nothing(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Not the total, not the count, not the fact that any of it
        exists."""
        calls = telegram_answers(monkeypatch, [{"amount": 360}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, STRANGER)

        response = client.get("/api/telegram/balance")

        assert calls == [], "Telegram was asked about the balance anyway"


class TestTheNumber:
    def test_it_is_the_sum_of_the_transactions(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        telegram_answers(
            monkeypatch, [{"amount": 360}, {"amount": 1080}, {"amount": 3240}]
        )
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        body = client.get("/api/telegram/balance").json()

        assert body["balance"] == 4680
        assert body["paidIn"] == 4680
        assert body["transactions"] == 3

    def test_a_withdrawal_comes_off_the_balance(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Telegram reports what left as well as what arrived, so the balance
        can fall below the total ever paid in."""
        telegram_answers(
            monkeypatch, [{"amount": 1080}, {"amount": -500}]
        )
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        body = client.get("/api/telegram/balance").json()

        assert body["balance"] == 580
        assert body["paidIn"] == 1080

    def test_no_made_up_minimum_is_offered(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Telegram does not publish the point at which a balance may be
        taken out. An invented threshold would send somebody off to wait for
        money that is never going to arrive, which is worse than not
        answering."""
        telegram_answers(monkeypatch, [{"amount": 1}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        body = client.get("/api/telegram/balance").json()

        assert body["withdrawable"] is None
        assert body["note"]

    def test_no_transactions_is_zero_not_an_error(self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        telegram_answers(monkeypatch, [])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        response = client.get("/api/telegram/balance")

        assert response.status_code == 200
        assert response.json()["balance"] == 0

    def test_telegram_refusing_is_reported_not_shown_as_zero(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """A bot with nothing in it and a bot Telegram would not answer about
        look identical if both said zero. Only one of them is true, and
        telling the owner the balance is empty when it is not would be the
        expensive way to be wrong."""

        async def refused(method: str, payload: Dict[str, Any]):
            return None

        monkeypatch.setattr(telegram_module, "_telegram_call", refused)
        monkeypatch.setenv("TELEGRAM_OWNER_ID", str(OWNER))
        as_user(app, OWNER)

        response = client.get("/api/telegram/balance")

        assert response.status_code == 502
        assert "balance" not in response.json()


class TestWhoseAccountIsTold:
    def test_the_owner_can_be_moved_without_a_redeploy(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        telegram_answers(monkeypatch, [{"amount": 360}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "4242")
        as_user(app, 4242)

        assert client.get("/api/telegram/balance").status_code == 200

    def test_an_unusable_owner_id_tells_nobody_rather_than_guessing(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """A typo must not hand the balance to whoever happens to ask."""
        telegram_answers(monkeypatch, [{"amount": 360}])
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "not-a-number")
        as_user(app, OWNER)

        assert client.get("/api/telegram/balance").status_code == 403


def _user(telegram_id: int):
    return telegram_module.TelegramUser(id=telegram_id, username="someone")