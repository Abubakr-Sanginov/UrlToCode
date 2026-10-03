"""Accounts, the projects they own, and what each account may still spend.

Two kinds of limit are enforced here, and they fail differently. A project
limit is a count: over the line, the save is refused. A daily allowance is
a spend: it has to be taken before the work starts, not after, because the
work has already been paid for by then. That is why the spend is a counter
row and not a computed sum - taking the unit and finding out afterwards
that there was none left is exactly how a limit gets exceeded.

Which database it lands in is not decided here: SQLite while developing,
Postgres - Neon - in production, and db.py is the only part that knows which
one answered. SQLite rather than a JSON file because these counters are read
and written at the same time by two requests, and a read-modify-write on a
file loses one of them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import db
from db import Database

# Kept as a module attribute because the tests point it at a temporary file.
# On Postgres nothing reads it - db.py decides the connection from DATABASE_URL
# - but SQLite does, and the tests rely on being able to redirect it.
DB_PATH = db.DEFAULT_SQLITE_PATH

# What each tier may do. "free" is the default for a new account and is
# deliberately stingy: it is enough to judge whether the tool works and
# not enough to sell the service.
TIERS: Dict[str, Dict[str, int]] = {
    "free": {"max_projects": 1, "daily_actions": 1, "shares_per_month": 0},
    "starter": {"max_projects": 10, "daily_actions": 10, "shares_per_month": 1},
    "pro": {"max_projects": 50, "daily_actions": 25, "shares_per_month": 10},
    "studio": {"max_projects": 300, "daily_actions": 100, "shares_per_month": 50},
    "unlimited": {
        "max_projects": 10_000,
        "daily_actions": 10_000,
        "shares_per_month": 10_000,
    },
}

# What Telegram keeps from every Stars payment, as a fraction.
STAR_COMMISSION = 0.30

# To be left with a dollar, the price has to be divided by what is left of
# it: 5 / 0.7 = $7.15. That is the 43% markup.
#
# Charged at $5.00 it arrives as $3.50, which is the number to remember -
# the whole difference between what a tier says and what it pays.
def markup() -> float:
    """The factor a price has to carry to survive the commission.

    A function rather than a constant so there is exactly one live input.
    Priced from a stored copy of the markup while the payout was worked out
    from the live commission, the two can drift apart and every price in the
    app is then quietly wrong.
    """
    return 1 / (1 - STAR_COMMISSION)


STAR_MARKUP = markup()  # 1.4286, for reading and for the docs

# How many Stars a dollar buys. Telegram's published rates put 100 Stars at
# about $1.99, which is 50 to the dollar.
#
# ASSUMED, not confirmed. Everything below is only as good as this line. If
# Telegram's rate is different, every price here moves with it, which is the
# point of computing them rather than typing them in.
STARS_PER_DOLLAR = 50

# What each paid tier is sold as. In dollars, because that is what the buyer
# is promised and what has to arrive.
TIER_DOLLARS: Dict[str, int] = {
    "starter": 5,
    "pro": 15,
    "studio": 45,
}


def stars_for(dollars: int) -> int:
    """What to charge, so that ``dollars`` is what is left afterwards.

    Rounded up to a multiple of 180, never down. A tier that pays $4.96 when
    it says $5 is a short the buyer finds out about, and one that pays $5.04
    just looks like rounding.
    """
    exact = dollars * markup() * STARS_PER_DOLLAR
    return int(math.ceil(exact / 180.0)) * 180


STAR_PRICES: Dict[str, int] = {
    tier: stars_for(dollars) for tier, dollars in TIER_DOLLARS.items()
}


def stars_net(stars: int) -> float:
    """What is left of a payment of this many Stars.

    The number that matters. What the buyer paid and what the seller keeps
    are different figures, and the difference is the whole reason a tier
    cannot be priced at its own price.
    """
    return round(stars / STARS_PER_DOLLAR * (1 - STAR_COMMISSION), 2)


def tariff() -> List[Dict[str, Any]]:
    """Every plan, at its price and what that price actually pays.

    Both figures are returned because showing only one of them is how a
    $5 tier quietly becomes a $3.50 one.
    """
    return [
        {
            "tier": tier,
            "dollars": TIER_DOLLARS[tier],
            "stars": price,
            "net": stars_net(price),
        }
        for tier, price in STAR_PRICES.items()
    ]

# A whole generating or editing run costs one unit, however many pages it
# touches: the user thinks of it as one action, and charging per page made
# a three page site cost three units before anything was shown.
DAILY_ACTIONS = ("generate", "edit")


@dataclass
class Account:
    id: int
    email: str
    tier: str
    created_at: float


@dataclass
class Usage:
    used: int
    remaining: int
    projects: int
    max_projects: int
    tier: str
    resets_at: float


class AccountError(Exception):
    """Something about the request is not acceptable."""


class NoCapacity(AccountError):
    """The account may not do this right now: over a limit."""


_lock = threading.Lock()
_initialised = False


def _connect() -> Database:
    # The tests redirect DB_PATH at a file of their own. Postgres has no file
    # to redirect, so the change only applies to SQLite, which is what those
    # tests run against.
    db.DEFAULT_SQLITE_PATH = DB_PATH
    return db.connect()


# The two shapes of the same four tables. SQLite takes an auto-numbered
# INTEGER PRIMARY KEY and REAL timestamps; Postgres has no lastrowid, so the
# counter has to be declared and the timestamps are double precision rather
# than REAL, which is a float too narrow to hold a unix time with its
# fractions.
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'free',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE (owner_id, run_id),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

-- One row per account, per kind of work, per day. The primary key
-- is the whole point: taking a unit is an INSERT that either
-- lands or conflicts, so two requests cannot both be the one
-- that was left.
CREATE TABLE IF NOT EXISTS usage (
    owner_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    day TEXT NOT NULL,
    spent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, kind, day),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS limit_overrides (
    owner_id INTEGER PRIMARY KEY,
    daily_actions INTEGER,
    max_projects INTEGER,
    note TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS oauth_identities (
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (provider, subject),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shares (
    token TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    page_path TEXT NOT NULL DEFAULT '/',
    created_at REAL NOT NULL,
    revoked_at REAL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_links (
    telegram_user_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    chat_id TEXT,
    username TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    charge_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    tier TEXT NOT NULL,
    stars INTEGER NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS projects_by_owner ON projects (owner_id);
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'free',
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE (owner_id, run_id),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS usage (
    owner_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    day TEXT NOT NULL,
    spent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, kind, day),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS limit_overrides (
    owner_id BIGINT PRIMARY KEY,
    daily_actions INTEGER,
    max_projects INTEGER,
    note TEXT NOT NULL DEFAULT '',
    updated_at DOUBLE PRECISION NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS oauth_identities (
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    owner_id BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (provider, subject),
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shares (
    token TEXT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    run_id TEXT NOT NULL,
    page_path TEXT NOT NULL DEFAULT '/',
    created_at DOUBLE PRECISION NOT NULL,
    revoked_at DOUBLE PRECISION,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_links (
    telegram_user_id TEXT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    chat_id TEXT,
    username TEXT NOT NULL DEFAULT '',
    updated_at DOUBLE PRECISION NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    charge_id TEXT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    tier TEXT NOT NULL,
    stars BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS projects_by_owner ON projects (owner_id);
"""


def _create_schema() -> None:
    with _connect() as db_:
        db_.script(_SQLITE_SCHEMA if not db.using_postgres() else _POSTGRES_SCHEMA)


def _ready() -> None:
    global _initialised
    with _lock:
        if not _initialised:
            _create_schema()
            _initialised = True


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """A password that cannot be read back out of the database."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def normalise_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise AccountError("That does not look like an email address.")
    return cleaned


def register(email: str, password: str) -> Account:
    """Create an account, or say plainly that the email is taken."""
    _ready()
    if len(password) < 8:
        raise AccountError("Use a password of at least 8 characters.")
    address = normalise_email(email)

    with _connect() as db_:
        existing = db_.execute(
            "SELECT id FROM accounts WHERE email = ?", (address,)
        ).fetchone()
        if existing is not None:
            raise AccountError("An account already uses that email.")
        try:
            account_id = db_.insert(
                "INSERT INTO accounts (email, password_hash, tier, created_at)"
                " VALUES (?, ?, 'free', ?)",
                (address, _hash_password(password), time.time()),
            )
        except db.integrity_error() as exc:
            # Two sign-ups for the same address at once; the constraint is
            # what decides, not the check above.
            raise AccountError("An account already uses that email.") from exc

    return Account(id=account_id, email=address, tier="free", created_at=time.time())


def authenticate(email: str, password: str) -> Optional[Account]:
    """The account for these credentials, or nothing at all.

    Nothing at all rather than a message: telling a stranger which half was
    wrong is free information about who has an account here.
    """
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, tier, created_at FROM accounts WHERE email = ?",
            (normalise_email(email),),
        ).fetchone()
    if row is None:
        return None
    if not _password_matches(password, str(row["password_hash"])):
        return None
    return Account(
        id=int(row["id"]),
        email=str(row["email"]),
        tier=str(row["tier"]),
        created_at=float(row["created_at"]),
    )


def account_by_id(account_id: int) -> Optional[Account]:
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, tier, created_at FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    if row is None:
        return None
    return Account(
        id=int(row["id"]),
        email=str(row["email"]),
        tier=str(row["tier"]),
        created_at=float(row["created_at"]),
    )


def set_tier(account_id: int, tier: str) -> None:
    """Raise or lower what an account may do. How a payment lands is not
    decided here; this is the door it opens."""
    _ready()
    if tier not in TIERS:
        raise AccountError(f"Unknown tier: {tier}")
    with _connect() as conn:
        conn.execute("UPDATE accounts SET tier = ? WHERE id = ?", (tier, account_id))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _midnight() -> float:
    """When today's allowance comes back, so the UI can say when."""
    now = time.localtime()
    tomorrow = time.localtime(time.mktime(now) + 86400)
    midnight = time.mktime(
        (
            tomorrow.tm_year, tomorrow.tm_mon, tomorrow.tm_mday,
            tomorrow.tm_hour, tomorrow.tm_min, 0, 0, 0, -1,
        )
    )
    return midnight


def _current_tier(account: Account) -> str:
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT tier FROM accounts WHERE id = ?", (account.id,)
        ).fetchone()
    return str(row["tier"]) if row is not None else account.tier


def _limits(account: Account) -> Dict[str, int]:
    """What this account may do, as it stands right now.

    Read back from the database rather than taken from the object in hand.
    An Account carries the tier it had when it was loaded, and raising a
    tier after a payment would otherwise leave every limit here unchanged -
    the change would take effect on the next sign-in and not before.

    Anything an administrator set for this account wins over its tier, so
    one person can be given more without moving everyone else on the plan.
    """
    limits = dict(TIERS.get(_current_tier(account), TIERS["free"]))
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT daily_actions, max_projects FROM limit_overrides WHERE owner_id = ?",
            (account.id,),
        ).fetchone()
    if row is not None:
        if row["daily_actions"] is not None:
            limits["daily_actions"] = int(row["daily_actions"])
        if row["max_projects"] is not None:
            limits["max_projects"] = int(row["max_projects"])
    return limits


def _start_of_month() -> float:
    when = time.localtime()
    return time.mktime(
        (when.tm_year, when.tm_mon, 1, 0, 0, 0, 0, 0, -1)
    )


def _new_share_token() -> str:
    """Something unguessable.

    The token is the whole access control on a shared site: there is no
    session, no cookie and nobody to ask. Anything short or patterned would
    let a link meant for one friend be walked to by anyone who guessed the
    next one.
    """
    return secrets.token_urlsafe(12)


def shares_used(account: Account) -> int:
    """How many links this account has made since the first of the month.

    A month rather than forever, so the allowance comes back on its own
    without anyone having to renew anything. Counted by when the link was
    made, not by whether it still works: revoking a link gives its slot
    back, but deleting it does not, because the making happened.
    """
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM shares WHERE owner_id = ? AND created_at >= ?",
            (account.id, _start_of_month()),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def create_share(account: Account, run_id: str, page_path: str = "/") -> Dict[str, Any]:
    """A link anyone can open, pointing at one of this account's runs.

    The allowance is taken and counted here rather than in the browser: the
    button can be hidden, but a request that was never charged is a request
    that can be repeated until it is not.
    """
    _ready()
    if not owns_project(account, run_id):
        # The same reason a project by run id cannot be opened by id: a run
        # id is a short string that shows up in lists and in websocket
        # traffic, and sharing one must not hand out somebody else's site.
        raise AccountError("That project is not yours to share.")

    limit = _limits(account)["shares_per_month"]
    used = shares_used(account)
    if limit <= 0:
        raise AccountError(
            "Sharing needs a paid plan. Subscribe to send someone a link"
            " to your site."
        )
    if used >= limit:
        raise AccountError(
            f"This plan makes {limit} link{'s' if limit != 1 else ''} a month."
            " They come back on the first."
        )

    token = _new_share_token()
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO shares (token, owner_id, run_id, page_path, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (token, account.id, run_id, page_path or "/", now),
        )
    return {
        "token": token,
        "runId": run_id,
        "pagePath": page_path or "/",
        "createdAt": now,
        "usedThisMonth": used + 1,
        "limitPerMonth": limit,
        "remainingThisMonth": max(limit - used - 1, 0),
    }


def list_shares(account: Account) -> List[Dict[str, Any]]:
    _ready()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token, run_id, page_path, created_at, revoked_at FROM shares"
            " WHERE owner_id = ? ORDER BY created_at DESC",
            (account.id,),
        ).fetchall()
    return [
        {
            "token": str(row["token"]),
            "runId": str(row["run_id"]),
            "pagePath": str(row["page_path"]),
            "createdAt": float(row["created_at"]),
            "revokedAt": (
                None if row["revoked_at"] is None else float(row["revoked_at"])
            ),
        }
        for row in rows
    ]


def revoke_share(account: Account, token: str) -> bool:
    """Take a link down.

    Marked rather than deleted: a revoked link must stop working, and
    leaving the row means the token can never be handed out a second time,
    so a link that was put out and then withdrawn cannot be guessed back
    into service.
    """
    _ready()
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE shares SET revoked_at = ? WHERE token = ? AND owner_id = ?"
            " AND revoked_at IS NULL",
            (time.time(), token, account.id),
        ).rowcount
    return changed == 1


def account_for_telegram(
    telegram_user_id: int,
    username: str,
    email_hint: str = "",
) -> Account:
    """The account behind a Telegram user.

    Matched on Telegram's own id, never on a name or an email address typed
    into the Mini App: those are things a stranger can write, and this is
    the same reasoning as the provider sign-ins, applied to Telegram.

    Deliberately takes no hint about who else might be signed in on this
    device. On a shared phone that hint belongs to whoever used it last,
    and following it would hand their clones and their paid links to the
    next person to open Telegram.
    """
    _ready()
    key = str(telegram_user_id)
    now = time.time()
    with _connect() as conn:
        linked = conn.execute(
            "SELECT accounts.id, accounts.email, accounts.tier, accounts.created_at"
            " FROM telegram_links JOIN accounts ON accounts.id = telegram_links.owner_id"
            " WHERE telegram_links.telegram_user_id = ?",
            (key,),
        ).fetchone()
        if linked is not None:
            conn.execute(
                "UPDATE telegram_links SET username = ?, updated_at = ?"
                " WHERE telegram_user_id = ?",
                (username, now, key),
            )
            return Account(
                id=int(linked["id"]),
                email=str(linked["email"]),
                tier=str(linked["tier"]),
                created_at=float(linked["created_at"]),
            )

        address = f"tg{telegram_user_id}@users.telegram"
        if "@" in email_hint:
            try:
                address = normalise_email(email_hint)
            except AccountError:
                address = f"tg{telegram_user_id}@users.telegram"
        # No password: this person signed in with a Telegram account, so
        # there is nothing to guess and nothing to send a reset to.
        try:
            account_id = conn.insert(
                "INSERT INTO accounts (email, password_hash, tier, created_at)"
                " VALUES (?, ?, 'free', ?)",
                (address, _hash_password(secrets.token_urlsafe(32)), now),
            )
        except db.integrity_error():
            found = conn.execute(
                "SELECT id FROM accounts WHERE email = ?", (address,)
            ).fetchone()
            if found is None:
                raise
            account_id = int(found["id"])

        conn.execute(
            "INSERT INTO telegram_links (telegram_user_id, owner_id, chat_id,"
            " username, updated_at) VALUES (?, ?, NULL, ?, ?)",
            (key, account_id, username, now),
        )
        row = conn.execute(
            "SELECT id, email, tier, created_at FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

    if row is None:  # pragma: no cover - the row was just written
        raise AccountError("The account could not be created.")
    return Account(
        id=int(row["id"]),
        email=str(row["email"]),
        tier=str(row["tier"]),
        created_at=float(row["created_at"]),
    )


def remember_telegram_chat(telegram_user_id: int, chat_id: str) -> None:
    """Where to send "your clone is ready".

    Kept only for a link that already exists, so a chat id arriving from a
    stranger's /start cannot quietly attach a stranger to somebody's
    account.
    """
    _ready()
    with _connect() as conn:
        conn.execute(
            "UPDATE telegram_links SET chat_id = ?, updated_at = ?"
            " WHERE telegram_user_id = ?",
            (chat_id, time.time(), str(telegram_user_id)),
        )


def telegram_chat_for(account: Account) -> Optional[str]:
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT chat_id FROM telegram_links WHERE owner_id = ?"
            " AND chat_id IS NOT NULL",
            (account.id,),
        ).fetchone()
    return None if row is None else str(row["chat_id"])


# How long an invoice stays payable. Telegram holds the payment screen open
# and a person walks away from it; this is generous rather than tight, and
# the signature is checked as well as the age.
INVOICE_TTL_SECONDS = 60 * 60


def sign_invoice(owner_id: int, tier: str, secret: str) -> str:
    """A short note saying what is being paid for, and who for.

    Sent to Telegram as the invoice payload and handed back when the
    payment lands. It has to carry its own proof because the only thing
    standing between this server and a forged "they paid for studio" is
    the signature on it.

    Signed rather than encrypted, so nothing here is secret from Telegram -
    it has to carry it back to us. What matters is that a third party
    cannot change a single character of it.
    """
    body = json.dumps(
        {
            "owner": owner_id,
            "tier": tier,
            "issued": int(time.time()),
            "nonce": secrets.token_urlsafe(9),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{base64.urlsafe_b64encode(body.encode()).decode()}.{signature}"


def read_invoice(payload: str, secret: str) -> Optional[Dict[str, Any]]:
    """What an invoice says, if it says anything at all.

    Every part of it is checked: the signature, that it was signed rather
    than guessed, and that it is not older than an hour. Returning None for
    anything doubtful keeps the caller from having to remember which of
    these it did.
    """
    encoded, _, signature = (payload or "").partition(".")
    if not encoded or not signature:
        return None
    try:
        body = base64.urlsafe_b64decode(encoded.encode()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        claim = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(claim, dict):
        return None
    try:
        issued = int(claim.get("issued", 0))
    except (TypeError, ValueError):
        return None
    if issued <= 0 or time.time() - issued > INVOICE_TTL_SECONDS:
        return None
    if claim.get("tier") not in STAR_PRICES:
        return None
    return cast(Dict[str, Any], claim)


def record_payment(
    charge_id: str, owner_id: int, tier: str, stars: int
) -> bool:
    """Note a payment and raise the plan. False if it was already counted.

    The charge id is the primary key, so the same payment arriving twice -
    and Telegram does repeat a webhook - is recorded once. Recording before
    granting is what makes that true: if the process dies between the two,
    the plan is not raised and the payment is visible as unpaid rather than
    paid for nothing.
    """
    _ready()
    with _connect() as conn:
        try:
            conn.insert(
                "INSERT INTO payments (charge_id, owner_id, tier, stars, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (charge_id, owner_id, tier, stars, time.time()),
            )
        except db.integrity_error():
            return False
        conn.execute(
            "UPDATE accounts SET tier = ? WHERE id = ?", (tier, owner_id)
        )
    return True


def payments_of(account: Account) -> List[Dict[str, Any]]:
    _ready()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT charge_id, tier, stars, created_at FROM payments"
            " WHERE owner_id = ? ORDER BY created_at DESC",
            (account.id,),
        ).fetchall()
    return [
        {
            "chargeId": str(row["charge_id"]),
            "tier": str(row["tier"]),
            "stars": int(row["stars"]),
            "createdAt": float(row["created_at"]),
        }
        for row in rows
    ]


def share_by_token(token: str) -> Optional[Dict[str, Any]]:
    """What a public link points at.

    The only lookup in the app that does not begin with an account. It is
    public because that is the point: a link sent to a friend has to open
    for someone with no account at all.
    """
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT run_id, page_path, revoked_at FROM shares WHERE token = ?",
            (token,),
        ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    return {"runId": str(row["run_id"]), "pagePath": str(row["page_path"])}


def set_limits(
    owner_id: int,
    daily_actions: Optional[int] = None,
    max_projects: Optional[int] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Give one account different limits from its tier.

    Either limit may be left unset to keep following the tier. The result is
    returned in full so an administrator can see what they actually did.
    """
    _ready()
    for value in (daily_actions, max_projects):
        if value is not None and value < 0:
            raise AccountError("A limit cannot be negative.")
        if value is not None and value > 1_000_000:
            raise AccountError("That limit is beyond anything this tool can use.")

    with _connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id = ?", (owner_id,)).fetchone()
        if row is None:
            raise AccountError("No such account.")
        conn.execute(
            "INSERT INTO limit_overrides"
            " (owner_id, daily_actions, max_projects, note, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (owner_id) DO UPDATE SET"
            " daily_actions = excluded.daily_actions,"
            " max_projects = excluded.max_projects,"
            " note = excluded.note,"
            " updated_at = excluded.updated_at",
            (owner_id, daily_actions, max_projects, note, time.time()),
        )
    return account_summary(owner_id)


def clear_limits(owner_id: int) -> Dict[str, Any]:
    """Send one account back to its tier's limits."""
    _ready()
    with _connect() as conn:
        conn.execute("DELETE FROM limit_overrides WHERE owner_id = ?", (owner_id,))
    return account_summary(owner_id)


def account_summary(owner_id: int) -> Dict[str, Any]:
    """One account as the administrator screen needs to show it."""
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, tier, created_at FROM accounts WHERE id = ?",
            (owner_id,),
        ).fetchone()
        if row is None:
            raise AccountError("No such account.")
        override = conn.execute(
            "SELECT daily_actions, max_projects, note FROM limit_overrides"
            " WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        today = _today()
        usage_rows = conn.execute(
            "SELECT kind, spent FROM usage WHERE owner_id = ? AND day = ?",
            (owner_id, today),
        ).fetchall()
        projects = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (owner_id,)
        ).fetchone()

    spent = {str(item["kind"]): int(item["spent"]) for item in usage_rows}
    account = Account(
        id=int(row["id"]),
        email=str(row["email"]),
        tier=str(row["tier"]),
        created_at=float(row["created_at"]),
    )
    limits = _limits(account)
    return {
        "id": account.id,
        "email": account.email,
        "tier": account.tier,
        "createdAt": account.created_at,
        "dailyActions": limits["daily_actions"],
        "maxProjects": limits["max_projects"],
        "usedGenerate": spent.get("generate", 0),
        "usedEdit": spent.get("edit", 0),
        "projects": int(projects["n"]),
        # Null means "whatever the tier says": the form shows the tier's
        # number and leaves the box alone until it is changed.
        "overrideDailyActions": None if override is None else override["daily_actions"],
        "overrideMaxProjects": None if override is None else override["max_projects"],
        "note": "" if override is None else str(override["note"]),
    }


def list_accounts() -> List[Dict[str, Any]]:
    """Every account, newest first, as the administrator screen lists them."""
    _ready()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM accounts ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [account_summary(int(row["id"])) for row in rows]


def usage_of(account: Account, kind: str = "generate") -> Usage:
    """What is left today, without spending any of it."""
    _ready()
    limits = _limits(account)
    with _connect() as conn:
        row = conn.execute(
            "SELECT spent FROM usage WHERE owner_id = ? AND kind = ? AND day = ?",
            (account.id, kind, _today()),
        ).fetchone()
        projects = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (account.id,)
        ).fetchone()
    spent = int(row["spent"]) if row is not None else 0
    allowance = limits["daily_actions"]
    return Usage(
        used=spent,
        remaining=max(allowance - spent, 0),
        projects=int(projects["n"]),
        max_projects=limits["max_projects"],
        tier=_current_tier(account),
        resets_at=_midnight(),
    )


def spend_action(account: Account, kind: str = "generate") -> Usage:
    """Take one unit of today's allowance, or refuse.

    The allowance is checked and taken in one statement, so two clicks that
    arrive together cannot both pass. Refusing after the model has already
    been paid for is the thing this exists to avoid.
    """
    if kind not in DAILY_ACTIONS:
        raise AccountError(f"Unknown action: {kind}")
    _ready()
    limits = _limits(account)
    allowance = limits["daily_actions"]
    day = _today()

    if allowance <= 0:
        # Refused before any statement runs. The insert below starts a
        # counter at 1 for a row that does not exist yet, and nothing in it
        # can hold that back - with no allowance there is no "less than" for
        # it to compare against. An administrator who sets a limit to zero
        # means none allowed, not one for free.
        raise NoCapacity(
            "This account has no allowance left today. Ask an administrator"
            " to raise it."
        )

    with _connect() as conn:
        took = conn.execute(
            "INSERT INTO usage (owner_id, kind, day, spent) VALUES (?, ?, ?, 1)"
            " ON CONFLICT (owner_id, kind, day) DO UPDATE SET spent = usage.spent + 1"
            " WHERE usage.spent < ?",
            (account.id, kind, day, allowance),
        )
        # One statement, not an INSERT followed by an UPDATE. It takes the
        # unit only if there was one to take, and says so by changing a row -
        # reading the total back cannot: landing exactly on the limit looks
        # the same whether this call took the last unit or was refused, and
        # reading it that way lets a request past the limit go unnoticed while
        # still counting as spent.
        #
        # One statement rather than two because a connection can die between
        # them. Had that happened, the row the first one created would be gone
        # and the second would update nothing - which looks exactly like
        # "refused": a user charged for nothing and told they are out of
        # allowance.
        took_one = took.rowcount == 1
        row = conn.execute(
            "SELECT spent FROM usage WHERE owner_id = ? AND kind = ? AND day = ?",
            (account.id, kind, day),
        ).fetchone()
        projects = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (account.id,)
        ).fetchone()

    spent = int(row["spent"]) if row is not None else 0
    if not took_one:
        raise NoCapacity(
            f"Today's allowance of {allowance} is used up. It comes back at"
            " midnight, or you can raise your limit."
        )
    return Usage(
        used=spent,
        remaining=max(allowance - spent, 0),
        projects=int(projects["n"]),
        max_projects=limits["max_projects"],
        tier=_current_tier(account),
        resets_at=_midnight(),
    )


def refund_action(account: Account, kind: str = "generate") -> Usage:
    """Give back one unit of today's allowance.

    The unit is taken before the work starts, because a limit checked
    afterwards is not a limit - by then the model has already been paid
    for. The price of taking it early is that a run which produces
    nothing must hand it back, or the user is charged for a failure they
    did not cause: a dead URL, a bot check, a provider that is down.

    Only the unit taken by this same run is returned, and only once. The
    counter is never driven below zero, so a double refund cannot invent
    extra allowance.
    """
    if kind not in DAILY_ACTIONS:
        raise AccountError(f"Unknown action: {kind}")
    _ready()
    day = _today()

    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE usage SET spent = spent - 1"
            " WHERE owner_id = ? AND kind = ? AND day = ? AND spent > 0",
            (account.id, kind, day),
        )
        refunded = cursor.rowcount == 1

    if not refunded:
        # Nothing was held, so there is nothing to return. Not an error:
        # the caller's intent - the user should not be charged - is
        # already satisfied.
        print(
            f"[ACCOUNTS] Refund for {account.email} ({kind}) found nothing to return"
        )
    return usage_of(account, kind)


def check_project_capacity(account: Account, run_id: str) -> None:
    """Refuse before the work, not after.

    Saving writes the whole project to disk first, so asking afterwards
    whether there was room means the user pays for a project nobody will
    ever be able to open.
    """
    _ready()
    limits = _limits(account)
    with _connect() as conn:
        already = conn.execute(
            "SELECT 1 FROM projects WHERE owner_id = ? AND run_id = ?",
            (account.id, run_id),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (account.id,)
        ).fetchone()
    if already is not None:
        return
    if int(count["n"]) >= limits["max_projects"]:
        raise NoCapacity(
            f"The free tier keeps {limits['max_projects']} project."
            if limits["max_projects"] == 1
            else f"This tier keeps {limits['max_projects']} projects."
        )


def note_project(
    account: Account, run_id: str, name: str, source_url: str = ""
) -> Usage:
    """Record a project that a run produced on its own.

    Different from add_project in what happens when the account is full:
    here the work is already done and already paid for, so a full project
    list is a reason to note it was not kept, never a reason to throw away
    a finished clone. The alternative - refusing - would tell someone who
    just paid for a working site that they may not have it.
    """
    try:
        return add_project(account, run_id, name, source_url)
    except NoCapacity:
        print(
            f"[ACCOUNTS] {account.email} has no free project slot for {run_id};"
            " the clone is kept but not listed"
        )
        return usage_of(account)


def add_project(account: Account, run_id: str, name: str, source_url: str = "") -> Usage:
    """Record a saved project, or refuse when the account is full."""
    _ready()
    limits = _limits(account)

    with _connect() as conn:
        already = conn.execute(
            "SELECT 1 FROM projects WHERE owner_id = ? AND run_id = ?",
            (account.id, run_id),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (account.id,)
        ).fetchone()
        if already is None and int(count["n"]) >= limits["max_projects"]:
            raise NoCapacity(
                f"The free tier keeps {limits['max_projects']} project."
                if limits["max_projects"] == 1
                else f"This tier keeps {limits['max_projects']} projects."
            )
        if already is None:
            conn.execute(
                "INSERT INTO projects (owner_id, run_id, name, source_url, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (account.id, run_id, name[:200], source_url[:2000], time.time()),
            )
            projects = conn.execute(
                "SELECT COUNT(*) AS n FROM projects WHERE owner_id = ?", (account.id,)
            ).fetchone()
        else:
            # Saving the same clone again is updating it, not adding one.
            # Counting it as a second filled the only slot the free tier has
            # and then refused, which reads as "you have too many" to someone
            # who has exactly one.
            projects = count

    return Usage(
        used=usage_of(account).used,
        remaining=usage_of(account).remaining,
        projects=int(projects["n"]),
        max_projects=limits["max_projects"],
        tier=_current_tier(account),
        resets_at=_midnight(),
    )


def list_projects(account: Account) -> List[Dict[str, Any]]:
    _ready()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT run_id, name, source_url, created_at FROM projects"
            " WHERE owner_id = ? ORDER BY created_at DESC",
            (account.id,),
        ).fetchall()
    return [
        {
            "runId": str(row["run_id"]),
            "name": str(row["name"]),
            "sourceUrl": str(row["source_url"]),
            "savedAt": float(row["created_at"]),
        }
        for row in rows
    ]


def account_for_provider(
    provider: str,
    subject: str,
    email: str,
    email_verified: bool,
) -> Account:
    """The account behind a signed-in identity at another provider.

    `subject` is the provider's own id for the person - a number GitHub and
    Google each keep for the lifetime of the account, and never reuse. That
    is what is matched on, and the email is only ever used to recognise an
    account that already exists.

    The verification flag is the whole point of the email being here. A
    provider that has not checked the address has told us nothing about who
    is holding it, so an unverified email is never enough to open an
    existing account: otherwise anyone able to set an unverified address to
    someone else's name walks straight into that person's projects. An
    unverified email still gets an account of its own, it just starts empty.

    An identity seen here before keeps its account no matter what the email
    says this time. A GitHub account whose address changes must not end up
    pointed at a different person.
    """
    _ready()
    address = normalise_email(email)
    now = time.time()

    with _connect() as conn:
        linked = conn.execute(
            "SELECT accounts.id, accounts.email, accounts.tier, accounts.created_at"
            " FROM oauth_identities JOIN accounts ON accounts.id = oauth_identities.owner_id"
            " WHERE oauth_identities.provider = ? AND oauth_identities.subject = ?",
            (provider, subject),
        ).fetchone()
        if linked is not None:
            return Account(
                id=int(linked["id"]),
                email=str(linked["email"]),
                tier=str(linked["tier"]),
                created_at=float(linked["created_at"]),
            )

        by_email = conn.execute(
            "SELECT id, email, tier, created_at FROM accounts WHERE email = ?",
            (address,),
        ).fetchone()
        account_id = int(by_email["id"]) if by_email is not None else 0

        if by_email is None:
            # Nothing to link to, verified or not. No password is set: this
            # person signed in with somebody else's login, and giving them
            # a password nobody chose would be inventing one on their behalf.
            random_password = secrets.token_urlsafe(32)
            try:
                account_id = conn.insert(
                    "INSERT INTO accounts (email, password_hash, tier, created_at)"
                    " VALUES (?, ?, 'free', ?)",
                    (address, _hash_password(random_password), now),
                )
            except db.integrity_error():
                # Another request created it between the read and here.
                found = conn.execute(
                    "SELECT id FROM accounts WHERE email = ?", (address,)
                ).fetchone()
                if found is None:
                    raise
                account_id = int(found["id"])
        elif not email_verified:
            # There is an account with this address, but this provider never
            # proved it is the same person. Start a separate one rather than
            # hand over the first one's projects.
            account_id = 0
            suffix = secrets.token_hex(4)
            alias = f"{address.split('@')[0]}+{suffix}@{address.split('@', 1)[1]}"
            random_password = secrets.token_urlsafe(32)
            try:
                account_id = conn.insert(
                    "INSERT INTO accounts (email, password_hash, tier, created_at)"
                    " VALUES (?, ?, 'free', ?)",
                    (alias, _hash_password(random_password), now),
                )
            except db.integrity_error() as exc:
                raise AccountError(
                    "That address is already in use. Sign in with your"
                    " password to reach the account it belongs to."
                ) from exc

        conn.execute(
            "INSERT INTO oauth_identities (provider, subject, owner_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (provider, subject, account_id, now),
        )

        # Read back inside the block: the account was created here, and the
        # details the caller gets are the ones the database now holds rather
        # than the ones this function assembled on the way in.
        row = conn.execute(
            "SELECT id, email, tier, created_at FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

    if row is None:  # pragma: no cover - the row was just written
        raise AccountError("The account could not be created.")
    return Account(
        id=int(row["id"]),
        email=str(row["email"]),
        tier=str(row["tier"]),
        created_at=float(row["created_at"]),
    )


def owns_project(account: Account, run_id: str) -> bool:
    """Whether this run id is one of this account's projects.

    Kept separate from list_projects because a run id is a short string
    that appears in the project list, in websocket traffic and in URLs.
    Every route that opens a project by that id asks this first.
    """
    _ready()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM projects WHERE owner_id = ? AND run_id = ?",
            (account.id, run_id),
        ).fetchone()
    return row is not None


def remove_project(account: Account, run_id: str) -> bool:
    _ready()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM projects WHERE owner_id = ? AND run_id = ?",
            (account.id, run_id),
        )
    return cursor.rowcount > 0


def new_session_token() -> str:
    return secrets.token_urlsafe(32)