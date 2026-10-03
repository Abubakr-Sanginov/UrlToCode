"""Turning a SQLite query into a Postgres one.

The two engines disagree about placeholders, about where a new row's id
comes from, and about the words CREATE TABLE has to be written with. The
accounts code is written once and run on either, so those three have to be
translated rather than remembered.

Nothing here talks to a database. It checks the translations on their own,
because a Postgres-only mistake otherwise waits until production to surface -
and this server has no Neon account to test against.
"""

from typing import Any, List

import pytest

import db


class test_placeholders:
    def test_sqlite_is_left_alone(self) -> None:
        db.DATABASE_URL = ""
        assert db.using_postgres() is False
        assert db.q("SELECT id FROM accounts WHERE email = ?") == (
            "SELECT id FROM accounts WHERE email = ?"
        )

    def test_postgres_gets_percent_placeholders(self) -> None:
        db.DATABASE_URL = "postgresql://u:p@host/db?sslmode=require"
        try:
            assert db.using_postgres() is True
            assert db.q("WHERE owner_id = ? AND kind = ?") == (
                "WHERE owner_id = %s AND kind = %s"
            )
        finally:
            db.DATABASE_URL = ""

    def test_every_placeholder_is_translated(self) -> None:
        db.DATABASE_URL = "postgres://u:p@host/db"
        try:
            translated = db.q("INSERT INTO usage (owner_id, kind, day) VALUES (?, ?, ?)")
            assert translated.count("%s") == 3
            assert "?" not in translated
        finally:
            db.DATABASE_URL = ""

    def test_a_postgres_url_is_recognised_with_either_spelling(self) -> None:
        # Neon shows postgresql://; some tools hand back postgres://.
        for url in ("postgresql://h/db", "postgres://h/db", "  POSTGRESQL://h/db  "):
            db.DATABASE_URL = url
            assert db.using_postgres() is True, url
        db.DATABASE_URL = ""

    def test_anything_else_is_not_postgres(self) -> None:
        for url in ("", "sqlite:///data/x.db", "mysql://h/db"):
            db.DATABASE_URL = url
            assert db.using_postgres() is False, url
        db.DATABASE_URL = ""


class test_the_two_schemas:
    def test_both_declare_the_same_four_tables(self) -> None:
        import accounts

        for name in ("accounts", "projects", "usage", "limit_overrides"):
            assert name in accounts._SQLITE_SCHEMA, name
            assert name in accounts._POSTGRES_SCHEMA, name

    def test_postgres_declares_its_own_counters(self) -> None:
        # SQLite's INTEGER PRIMARY KEY AUTOINCREMENT is a rowid alias. Postgres
        # has no rowid, so without an identity column the first insert fails.
        import accounts

        assert "GENERATED ALWAYS AS IDENTITY" in accounts._POSTGRES_SCHEMA
        assert "AUTOINCREMENT" not in accounts._POSTGRES_SCHEMA

    def test_postgres_timestamps_are_not_real(self) -> None:
        # REAL is a float32 in Postgres, which cannot hold a unix time with
        # its fractional part to the second. Timestamps are compared and
        # sorted, so that truncation is not cosmetic.
        import accounts

        assert "REAL" not in accounts._POSTGRES_SCHEMA
        assert "DOUBLE PRECISION" in accounts._POSTGRES_SCHEMA

    def test_the_usage_key_is_still_the_whole_point(self) -> None:
        import accounts

        for schema in (accounts._SQLITE_SCHEMA, accounts._POSTGRES_SCHEMA):
            assert "PRIMARY KEY (owner_id, kind, day)" in schema


class test_integrity_errors:
    def test_each_engine_raises_its_own(self) -> None:
        import sqlite3

        db.DATABASE_URL = ""
        assert db.integrity_error() is sqlite3.IntegrityError
        db.DATABASE_URL = ""

    def test_postgres_names_the_constraint_it_broke(self) -> None:
        db.DATABASE_URL = "postgresql://u:p@host/db"
        try:
            from psycopg.errors import UniqueViolation

            assert db.integrity_error() is UniqueViolation
        finally:
            db.DATABASE_URL = ""


class test_the_startup_line:
    def test_it_names_the_database(self) -> None:
        db.DATABASE_URL = ""
        assert "sqlite" in db.describe()

    def test_it_never_prints_the_password(self) -> None:
        # This string ends up in logs and screenshots. A Neon URL carries the
        # password in it, and showing it here would put it in all of them.
        db.DATABASE_URL = "postgresql://admin:hunter2@ep-abc.us-east-2.aws.neon.tech/neondb?sslmode=require"
        try:
            described = db.describe()
            assert "hunter2" not in described
            assert "ep-abc.us-east-2.aws.neon.tech" in described
            assert "admin" in described
        finally:
            db.DATABASE_URL = ""


class test_statements_are_split_for_postgres:
    def test_one_statement_becomes_one(self) -> None:
        parts = list(db._split_statements("CREATE TABLE a (id INT)"))
        assert parts == ["CREATE TABLE a (id INT)"]

    def test_several_become_several(self) -> None:
        parts = list(db._split_statements("CREATE TABLE a (id INT); CREATE TABLE b (id INT);"))
        assert parts == ["CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"]

    def test_blank_remainders_are_dropped(self) -> None:
        parts = list(db._split_statements("CREATE TABLE a (id INT);\n\n  \n"))
        assert parts == ["CREATE TABLE a (id INT)"]


class test_the_whole_schema_runs_on_sqlite:
    def test_it_creates(self) -> None:
        # Not a mock: the statements are handed to the engine that will
        # actually run them locally, so a typo in the SQLite half of this
        # shows up here rather than on first use.
        import accounts
        import sqlite3

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(accounts._SQLITE_SCHEMA)
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"accounts", "projects", "usage", "limit_overrides"} <= names


def test_rows_are_readable_by_name() -> None:
    # Everything above this module reads row["column"], so a driver that
    # handed back tuples instead would break every call at once.
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    rows = db._rows_from(_FakeCursor([{"id": 7}]))
    assert rows[0]["id"] == 7


class _FakeCursor:
    """The shape db._rows_from asks of a finished statement."""

    description = [("id",)]
    rowcount = 1

    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def fetchall(self) -> Any:
        return self._rows

    def close(self) -> None:
        return None


class _FailingCursor(_FakeCursor):
    """A cursor on a connection the far end has already closed."""

    description = [("id",)]
    rowcount = -1

    def __init__(self) -> None:
        self.closed = False

    def execute(self, sql: str, params: Any) -> None:
        from psycopg import OperationalError

        raise OperationalError("SSL connection has been closed unexpectedly")

    def fetchall(self) -> Any:
        return []

    def close(self) -> None:
        self.closed = True


class _FailingConnection:
    def __init__(self) -> None:
        self.rolled_back = False

    def cursor(self) -> Any:
        return _FailingCursor()

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        return None


class test_a_connection_that_died:
    """
    Neon closes connections from its side, and a socket that has been closed
    over there reads as perfectly fine from here until something is written
    to it. The failure this covers is the one that actually happened: the
    server was quiet for a while, Neon dropped the idle connection, and the
    next request - the first clone - died with "SSL connection has been
    closed unexpectedly".
    """

    def _postgres_mode(self, monkeypatch: Any) -> None:
        db.DATABASE_URL = "postgresql://u:p@host/db"

    def test_the_statement_runs_again_on_a_new_connection(
        self, monkeypatch: Any
    ) -> None:
        self._postgres_mode(monkeypatch)
        dead = _FailingConnection()
        alive = _FakeConnection()
        # Only the replacement is queued: `dead` went to the Database
        # directly, standing in for one the pool had already handed out.
        handed_out = [alive]
        monkeypatch.setattr(db, "_raw_connect", lambda: handed_out.pop(0))
        returned: List[Any] = []
        monkeypatch.setattr(
            db, "_return_to_pool", lambda connection: returned.append(connection)
        )

        try:
            with db.Database(dead, lambda c: returned.append(c)) as conn:
                result = conn.execute("SELECT id FROM accounts WHERE id = ?", (1,))
        finally:
            db.DATABASE_URL = ""

        assert result.fetchone() is not None
        # The dead one is handed back the moment it is found dead, rather
        # than kept or quietly reused: a pool that got a broken connection
        # returned to it would hand the same corpse out again. The healthy
        # replacement goes back at the end of the block as usual.
        assert returned == [dead, alive]

    def test_it_only_retries_once(
        self, monkeypatch: Any
    ) -> None:
        # A second failure means the database is genuinely unreachable.
        # Retrying forever would turn an outage into a hang.
        self._postgres_mode(monkeypatch)
        connections = [_FailingConnection(), _FailingConnection()]
        monkeypatch.setattr(db, "_raw_connect", lambda: connections.pop(0))
        monkeypatch.setattr(db, "_return_to_pool", lambda connection: None)
        conn = db.Database(connections[0], lambda c: None)

        try:
            from psycopg import OperationalError

            with pytest.raises(OperationalError):
                conn.execute("SELECT 1")
        finally:
            db.DATABASE_URL = ""

    def test_sqlite_does_not_retry(
        self, monkeypatch: Any
    ) -> None:
        # Nothing to reconnect to, and a real bug would otherwise be retried
        # once and look like a blip.
        db.DATABASE_URL = ""
        connections = [_FailingConnection(), _FailingConnection()]
        monkeypatch.setattr(db, "_raw_connect", lambda: connections.pop(0))
        conn = db.Database(connections[0], lambda c: None)

        from psycopg import OperationalError

        with pytest.raises(OperationalError):
            conn.execute("SELECT 1")
        assert len(connections) == 2, "SQLite should not have tried a reconnect"


class _FakeConnection:
    """A connection that works."""

    row_factory = None

    def __init__(self) -> None:
        self.queries: list = []

    def cursor(self) -> Any:
        return _RecordingCursor(self.queries)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


class _RecordingCursor(_FakeCursor):
    def __init__(self, queries: list) -> None:
        super().__init__([{"id": 1, "n": 3}])
        self._queries = queries

    def execute(self, sql: str, params: Any) -> None:
        self._queries.append((sql, params))