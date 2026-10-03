"""Checking what Telegram signed, and what a stranger would try instead.

The blob is the only thing separating this app from anybody claiming to be
anybody, so these tests spend most of their time on forgery.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, cast
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import accounts as accounts_module
import routes.url_to_code as url_to_code
import telegram_auth
from routes import accounts as accounts_route
from routes import telegram as telegram_module

BOT_TOKEN = "123456789:AAF-very-secret-token-value-for-tests-only"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    yield


def make_init_data(
    telegram_id: int = 777,
    first_name: str = "Ada",
    username: str = "ada",
    token: str = BOT_TOKEN,
    auth_date: Optional[int] = None,
    extra: Optional[Dict[str, str]] = None,
) -> str:
    """A blob signed the way Telegram signs one."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAA",
        "user": json.dumps(
            {
                "id": telegram_id,
                "first_name": first_name,
                "username": username,
                "language_code": "en",
            }
        ),
    }
    fields.update(extra or {})
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TestSignature:
    def test_a_real_blob_is_accepted(self):
        user = telegram_auth.verify(make_init_data(), BOT_TOKEN)
        assert user is not None
        assert user.id == 777
        assert user.username == "ada"

    def test_somebody_who_guesses_a_user_id_is_refused(self):
        # The whole point. The id is the one thing a stranger would like to
        # write, and it is inside the signed part.
        blob = make_init_data()
        forged = blob.replace("777", "888")
        assert telegram_auth.verify(forged, BOT_TOKEN) is None

    def test_a_blob_signed_with_another_bot_token_is_refused(self):
        blob = make_init_data(token="999999999:another-bot-token-entirely-here")
        assert telegram_auth.verify(blob, BOT_TOKEN) is None

    def test_changing_the_name_after_signing_is_refused(self):
        blob = make_init_data(first_name="Ada")
        assert telegram_auth.verify(blob.replace("Ada", "Eve"), BOT_TOKEN) is None

    def test_adding_a_field_after_signing_is_refused(self):
        blob = make_init_data()
        assert (
            telegram_auth.verify(blob + "&start_param=someone-elses-project", BOT_TOKEN)
            is None
        )

    def test_the_signature_key_telegram_adds_is_not_part_of_the_check(self):
        # Newer blobs carry a third-party signature beside the hash. It is
        # not part of what Telegram signed, so it must not change the
        # signed string or every current client would be refused.
        blob = make_init_data()
        user = telegram_auth.verify(blob + "&signature=ignored-here", BOT_TOKEN)
        assert user is not None

    def test_rubbish_is_refused(self):
        assert telegram_auth.verify("", BOT_TOKEN) is None
        assert telegram_auth.verify("not-url-encoded", BOT_TOKEN) is None
        assert telegram_auth.verify("hash=abc", BOT_TOKEN) is None

    def test_a_blob_does_not_go_on_working_forever(self):
        # Telegram never expires one. Without a check here, a blob pasted
        # into a script stays valid indefinitely.
        old = make_init_data(auth_date=int(time.time()) - 60 * 60 * 48)
        assert telegram_auth.verify(old, BOT_TOKEN) is None

    def test_a_blob_from_the_future_is_refused(self):
        ahead = make_init_data(auth_date=int(time.time()) + 3600)
        assert telegram_auth.verify(ahead, BOT_TOKEN) is None

    def test_no_token_means_nothing_is_verifiable(self):
        assert telegram_auth.verify(make_init_data(), "") is None

    def test_a_half_copied_token_is_caught_by_the_shape_check(self):
        assert telegram_auth.is_bot_token_usable(BOT_TOKEN) is True
        assert telegram_auth.is_bot_token_usable("") is False
        assert telegram_auth.is_bot_token_usable("123:short") is False
        assert telegram_auth.is_bot_token_usable("no-colon-at-all") is False


class TestMiniAppSignIn:
    @pytest.fixture
    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(accounts_route.router)
        return TestClient(app)

    def test_a_real_blob_signs_someone_in(self, client: TestClient):
        response = client.post(
            "/api/auth/telegram", json={"initData": make_init_data()}
        )

        assert response.status_code == 200
        assert response.json()["account"]["email"] == "tg777@users.telegram"
        assert accounts_route.SESSION_COOKIE in response.cookies

    def test_a_forged_blob_is_refused(self, client: TestClient):
        forged = make_init_data().replace("777", "888")

        response = client.post("/api/auth/telegram", json={"initData": forged})

        assert response.status_code == 401
        assert accounts_route.SESSION_COOKIE not in response.cookies

    def test_an_empty_blob_is_refused(self, client: TestClient):
        assert client.post("/api/auth/telegram", json={"initData": ""}).status_code == 401

    def test_the_same_person_comes_back_to_the_same_account(self, client: TestClient):
        first = client.post("/api/auth/telegram", json={"initData": make_init_data()})
        second = client.post("/api/auth/telegram", json={"initData": make_init_data()})
        assert first.json()["account"]["id"] == second.json()["account"]["id"]

    def test_two_people_get_two_accounts(self, client: TestClient):
        one = client.post("/api/auth/telegram", json={"initData": make_init_data(777)})
        two = client.post("/api/auth/telegram", json={"initData": make_init_data(888)})
        assert one.json()["account"]["id"] != two.json()["account"]["id"]

    def test_the_next_person_on_a_shared_device_does_not_inherit_the_account(self, client: TestClient):
        """The bug this guards.

        The cookie from the first person rides along on the second
        person's request. If this endpoint believed it, opening Telegram on
        a shared phone would show the previous user's clones and paid links.
        The signed blob is the only thing that decides who this is.
        """
        first = client.post("/api/auth/telegram", json={"initData": make_init_data(777)})
        assert first.status_code == 200

        second = client.post("/api/auth/telegram", json={"initData": make_init_data(888)})

        assert second.json()["account"]["id"] != first.json()["account"]["id"]
        assert second.json()["account"]["email"] == "tg888@users.telegram"

    def test_a_password_account_is_not_silently_reused(self, client: TestClient):
        """Cost of the rule above, stated so it is not mistaken for free.

        Somebody with both a password account and Telegram now has two.
        Linking them is a deliberate act rather than something a cookie on a
        shared device can do by accident.
        """
        existing = accounts_module.register("real@test.dev", "correct horse battery")

        response = client.post("/api/auth/telegram", json={"initData": make_init_data(777)})

        assert response.json()["account"]["id"] != existing.id

    def test_the_account_has_no_password_to_guess(self, client: TestClient):
        client.post("/api/auth/telegram", json={"initData": make_init_data()})

        found = accounts_module.authenticate("tg777@users.telegram", "")
        assert found is None


class TestWebhook:
    @pytest.fixture
    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(telegram_module.router)
        return TestClient(app)

    def test_a_post_without_the_secret_is_refused(self, client: TestClient):
        # The webhook address is public. Anyone who finds it must not be
        # able to make the bot talk to somebody else's account.
        response = client.post(
            "/api/telegram/webhook",
            json={"update_id": 1, "message": {"text": "/start", "chat": {"id": 5}}},
        )
        assert response.status_code == 403

    def test_a_post_with_the_wrong_secret_is_refused(self, client: TestClient):
        response = client.post(
            "/api/telegram/webhook",
            json={"update_id": 1, "message": {"text": "/start"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "guess"},
        )
        assert response.status_code == 403

    def test_a_post_without_a_configured_secret_refuses_everything(self, client: TestClient, monkeypatch):
        # Refusing when there is nothing to check against is the safe
        # direction: better a silent bot than an open one.
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
        response = client.post(
            "/api/telegram/webhook", json={"update_id": 1, "message": {"text": "/start"}}
        )
        assert response.status_code == 503

    def test_a_start_from_telegram_is_answered(self, client: TestClient, monkeypatch):
        sent = {}

        async def fake_send(chat_id, text, reply_markup=None):
            sent["chat_id"] = chat_id
            sent["text"] = text
            sent["reply_markup"] = reply_markup
            return True

        monkeypatch.setattr(telegram_module, "send_message", fake_send)

        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {"text": "/start", "chat": {"id": 4242}, "from": {"id": 4242}},
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        )

        assert response.status_code == 200
        assert sent["chat_id"] == "4242"
        # The button is what makes this a Mini App rather than a chat
        # partner: a web_app keyboard button is the only way Telegram lets
        # this page open as an app rather than as a website.
        button: Dict[str, Any] = cast(Dict[str, Any], sent["reply_markup"])["inline_keyboard"][0][0]
        assert button["text"] == "Open UrlToCode"
        assert button["web_app"]["url"].endswith("/")

    def test_ordinary_chatter_is_ignored(self, client: TestClient):
        response = client.post(
            "/api/telegram/webhook",
            json={"update_id": 1, "message": {"text": "hello", "chat": {"id": 1}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        )
        assert response.status_code == 200


class TestStatus:
    def test_it_never_prints_the_token(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(telegram_module.router)
        body = TestClient(app).get("/api/telegram/status").json()

        assert BOT_TOKEN not in json.dumps(body)
        assert body["configured"] is True

    def test_a_missing_token_is_reported_as_missing(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        app = FastAPI()
        app.include_router(telegram_module.router)
        body = TestClient(app).get("/api/telegram/status").json()

        assert body["configured"] is False


class TestNotifications:
    async def test_nobody_is_told_about_a_clone_they_never_asked_for(self, monkeypatch):
        # A message to a stranger is worse than no message. Somebody who has
        # an account but has never spoken to the bot is exactly that.
        called = []

        async def spy(chat_id, text, reply_markup=None):
            called.append(chat_id)
            return True

        monkeypatch.setattr(telegram_module, "send_message", spy)
        account = accounts_module.register("quiet@test.dev", "correct horse battery")

        assert await telegram_module.announce_ready(account, "Site") is False
        assert called == []

    async def test_somebody_who_used_the_bot_is_told(self, monkeypatch):
        async def spy(
            chat_id: str, text: str, reply_markup: Optional[Dict[str, Any]] = None
        ) -> bool:
            return chat_id == "42"

        monkeypatch.setattr(telegram_module, "send_message", spy)
        # The path a real person takes: opened the Mini App, then pressed
        # /start. Both are needed - the account exists only after the first.
        account = accounts_module.account_for_telegram(99, "ada")
        accounts_module.remember_telegram_chat(99, "42")

        assert await telegram_module.announce_ready(account, "Site") is True

    async def test_a_failed_send_never_breaks_the_clone_it_was_about(self, monkeypatch):
        # The person is waiting on a clone, not on a notification about it.
        # A dead network must not turn into an error on the run itself.
        monkeypatch.setattr(
            telegram_module, "send_message", _raising_send
        )
        account = accounts_module.register("resilient@test.dev", "correct horse battery")

        assert await telegram_module.announce_ready(account, "Site") is False


async def _raising_send(chat_id, text, reply_markup=None) -> bool:
    raise RuntimeError("network gone")


class TestItIsActuallyWiredUp:
    def test_a_finished_clone_is_announced(self):
        """Nothing else calls this.

        A notification function that only tests call is the same as no
        notification at all, and it looks finished on a skim.
        """
        source = Path(url_to_code.__file__).read_text(encoding="utf-8")

        assert "await announce_ready(" in source

    def test_it_is_announced_exactly_once(self):
        # Once per file, and not anywhere that handles a failure. The
        # import has no parentheses, so this counts the call alone.
        source = Path(url_to_code.__file__).read_text(encoding="utf-8")
        assert source.count("await announce_ready(") == 1