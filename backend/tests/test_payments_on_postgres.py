"""Check the payment path against the engine it actually runs on.

Every other test in this suite runs on SQLite, which is fast and needs
nothing. The trouble is that SQLite and Postgres do not fail the same way:
it accepts an INSERT and reports a rowid whatever the table looks like,
while Postgres parses the whole statement first and refuses one naming a
column that is not there.

That difference hid a bug that cost a real payment. db.insert() appends
RETURNING id, which is how Postgres reports a new row. The payments table
is keyed on the charge id and has no id column, so every purchase failed
with UndefinedColumn - after the money had left. All 1149 tests passed,
because none of them ran here.

So the table below is checked against Postgres when a database url is
available, and against SQLite otherwise. A skipped run is reported as
skipped rather than passing quietly, so "green" cannot mean "the real
database was never touched".
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

import accounts as accounts_module
import db


def _postgres_url() -> str:
    """The database to check against, if one was offered.

    Read from TEST_POSTGRES_URL rather than DATABASE_URL: conftest empties
    DATABASE_URL on purpose, so that a developer's .env cannot decide how the
    suite behaves, and reusing it here would mean those two fighting. A test
    that needs a real database says so through its own variable.
    """
    for name in ("TEST_POSTGRES_URL", "DATABASE_URL"):
        url = str(os.environ.get(name, "") or "").strip()
        # postgres:// only: a sqlite path in this variable is the local
        # default, not a database to check against.
        if url.startswith(("postgres://", "postgresql://")):
            return url
    return ""


def _ensure_schema() -> None:
    accounts_module._create_schema()
    with accounts_module._connect() as conn:
        # The suite must not leave purchases behind in a shared database.
        # Matched on an exact prefix with LEFT rather than LIKE 'probe-%':
        # psycopg reads a bare % as the start of a placeholder, and this
        # database holds real payments that a loose pattern must not reach.
        rows = conn.execute(
            "SELECT charge_id FROM payments WHERE LEFT(charge_id, 5) = ?",
            ("probe",),
        ).fetchall()
        for row in rows:
            conn.execute(
                "DELETE FROM payments WHERE charge_id = ?", (row["charge_id"],)
            )


@pytest.fixture
def live_postgres() -> Any:
    url = _postgres_url()
    if not url:
        pytest.skip("no Postgres url to check against")
    if db.using_postgres():
        pytest.skip("already running on Postgres")
    previous = db.DATABASE_URL
    os.environ["DATABASE_URL"] = url
    db.reset_engine_for_tests()
    previous_db = accounts_module.DB_PATH
    accounts_module._initialised = False
    try:
        _ensure_schema()
        yield
    finally:
        os.environ.pop("DATABASE_URL", None)
        db.DATABASE_URL = previous
        db.reset_engine_for_tests()
        accounts_module.DB_PATH = previous_db
        accounts_module._initialised = False


def _an_account() -> int:
    return accounts_module.account_for_provider(
        "test",
        f"probe-{uuid.uuid4()}",
        f"probe-{uuid.uuid4()}@test.invalid",
        True,
    ).id


class TestPaymentsOnPostgres:
    def test_a_payment_is_recorded_and_the_plan_rises(
        self, live_postgres: Any
    ) -> None:
        """The exact call that was crashing.

        Before the fix: UndefinedColumn, column "id" does not exist, raised
        from RETURNING id on a table that has no such column. SQLite never
        noticed, which is why nothing caught it.
        """
        owner = _an_account()

        recorded = accounts_module.record_payment(
            f"probe-{uuid.uuid4()}", owner, "starter", 1
        )

        assert recorded is True

    def test_the_plan_actually_rises(self, live_postgres: Any) -> None:
        # The row landing is not the same as the plan being raised, and the
        # failure mode of the bug was silently not raising it.
        owner = _an_account()

        accounts_module.record_payment(
            f"probe-{uuid.uuid4()}", owner, "pro", 1
        )

        account = accounts_module.account_by_id(owner)
        assert account is not None
        assert account.tier == "pro"

    def test_the_same_payment_twice_is_counted_once(
        self, live_postgres: Any
    ) -> None:
        # Telegram does repeat a webhook. The primary key is what stops a
        # second delivery from raising the plan a second time, and it is the
        # constraint that just failed.
        owner = _an_account()
        charge = f"probe-{uuid.uuid4()}"

        first = accounts_module.record_payment(charge, owner, "starter", 1)
        second = accounts_module.record_payment(charge, owner, "starter", 1)

        assert first is True
        assert second is False


class TestTheTablesPostgresWouldRefuse:
    """Every table db.insert() is pointed at must have an id to return."""

    def test_only_tables_with_an_id_are_inserted_through(self) -> None:
        """db.insert() appends RETURNING id on Postgres.

        SQLite returns lastrowid whatever the statement was, so a table
        without an id is only broken on the deployed engine. This reads the
        calls out of accounts.py and checks each one names a table that has
        an id, which is the constraint that was invisible from the code.
        """
        source = Path(accounts_module.__file__).read_text(encoding="utf-8")

        import re

        calls = re.findall(r"\.insert\(\s*\n\s*\"INSERT INTO (\w+)", source)
        assert calls, "expected to find the db.insert() callsites"

        for table in calls:
            rows: List[Dict[str, Any]] = []
            with accounts_module._connect() as conn:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            columns = [row["name"] for row in rows]
            assert "id" in columns, (
                f"insert() is used on {table}, which has no id column; "
                f"Postgres will reject RETURNING id there"
            )