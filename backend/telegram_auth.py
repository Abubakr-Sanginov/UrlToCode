"""Checking that a Mini App really was opened by your bot.

Telegram puts a signed blob of strings into the page. That blob is the only
thing standing between this app and anyone who decides to be whoever they
like by writing the user id they fancy into the request - so it is checked
on the server, on every call that acts as that person, and the client's word
means nothing.

The algorithm is Telegram's, not ours:

* drop ``hash`` from the blob, sort what is left, and join it as
  ``key=value`` lines - that is the string Telegram signed;
* the key to sign it with is itself a signature: HMAC of the literal
  ``WebAppData`` under the bot token;
* compare that signature with the one that arrived.

Three things are worth noticing, and each one is a hole if left out.
The check is a signature rather than a call back to Telegram, so it costs
nothing and cannot be defeated by Telegram being slow. It is a comparison
with ``compare_digest`` rather than ``==``, so it does not leak how much of
the guess was right. And the blob is accepted only for a while, because it
does not expire on its own - a blob pasted into a script works forever
unless something here refuses it later.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

# How long a blob stays good for. Telegram does not expire one, so without
# this a blob captured from a log, a screenshot or an old chat is a
# permanent way in.
MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    photo_url: str = ""

    @property
    def display_name(self) -> str:
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) or (f"@{self.username}" if self.username else str(self.id))


def secret_key(bot_token: str) -> bytes:
    """The key Telegram signed the blob with.

    Derived from the bot token, which only the bot and Telegram know. It is
    a signature rather than the token itself so the token is never used
    directly as a key.
    """
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def check_signature(init_data: str, bot_token: str) -> Optional[Dict[str, str]]:
    """The fields in this blob, if the blob is ours and unaltered."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None

    received = pairs.pop("hash", "")
    # A key that arrived beside the hash in newer blobs. Not signed, so it
    # must not be part of what is verified.
    pairs.pop("signature", None)
    if not received:
        return None

    # Telegram signs the fields in alphabetical order, joined by newlines,
    # with no sorting the client could disagree about.
    data_check_string = "\n".join(
        f"{key}={pairs[key]}" for key in sorted(pairs.keys())
    )
    expected = hmac.new(
        secret_key(bot_token), data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    return pairs


def verify(init_data: str, bot_token: str) -> Optional[TelegramUser]:
    """Who opened the Mini App, or nothing if the blob cannot be trusted."""
    fields = check_signature(init_data, bot_token)
    if fields is None:
        return None

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date <= 0:
        return None
    # Telegram's clock and ours are close enough for this, and a blob that
    # claims to be from the future is as suspicious as one from last year.
    if time.time() - auth_date > MAX_AGE_SECONDS:
        return None
    if auth_date - time.time() > 60:
        return None

    profile: Dict[str, Any] = {}
    try:
        decoded = json.loads(fields.get("user", "{}"))
        if isinstance(decoded, dict):
            profile = dict(decoded)
    except json.JSONDecodeError:
        return None

    raw_id: Any = profile.get("id")
    try:
        telegram_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    return TelegramUser(
        id=telegram_id,
        first_name=str(profile.get("first_name") or ""),
        last_name=str(profile.get("last_name") or ""),
        username=str(profile.get("username") or ""),
        language_code=str(profile.get("language_code") or ""),
        photo_url=str(profile.get("photo_url") or ""),
    )


def is_bot_token_usable(token: str) -> bool:
    """Whether this looks like a bot token.

    Checked at startup so a missing or half-copied token is said out loud
    rather than showing up later as every Mini App sign-in failing with
    "that is not Telegram".
    """
    cleaned = (token or "").strip()
    if not cleaned:
        return False
    # <id>:<35 characters of base64url>. The shape is all that is checked -
    # a well-formed token that is not real fails at the first API call.
    head, _, rest = cleaned.partition(":")
    if not head.isdigit() or len(rest) < 30:
        return False
    return all(character.isalnum() or character in "_-" for character in rest)