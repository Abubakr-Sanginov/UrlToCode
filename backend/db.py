"""Which database this server is talking to, and the few differences that follow.

SQLite while developing, Postgres - Neon - when DATABASE_URL says so. The
accounts code is written once against this module and never asks which one it
got, so switching between the two is a setting rather than a rewrite.

What actually differs, and is papered over here:

* Placeholders. SQLite wants ``?``, Postgres wants ``%s``. The queries are
  written with ``?`` because that is what SQLite wants and what reads most
  clearly, and :func:`q` translates on the way out. A literal ``%`` inside a
  query would be misread as a placeholder, so ``%%`` escapes it.

* New rows. SQLite's ``INTEGER PRIMARY KEY AUTOINCREMENT`` hands back the id
  through ``cursor.lastrowid``. Postgres has no such thing: the id only comes
  back if the INSERT says ``RETURNING id``. :meth:`Database.insert` writes that
  for you, so callers never spell it.

* Transactions. Both drivers commit when the ``with`` block ends and neither
  closes the connection there. Closing matters more than usual on Neon, which
  caps how many connections a branch may hold, so the block closes too.

Rows come back the same way from both - a mapping that can be read by column
name - because the code above this module is written against that and nothing
else has to change.
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Type

# Where SQLite lives when nothing else is configured.
DEFAULT_SQLITE_PATH = Path(os.environ.get("ACCOUNTS_DB", "data/accounts.db"))

# Neon hands out a long URL like postgresql://user:pw@ep-xxx.region.aws.neon.tech/db?sslmode=require
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()


def using_postgres() -> bool:
    """True when this process should talk to Postgres rather than a file.

    Stripped and lowercased because the value arrives from .env, from a
    platform's secret store, or from somewhere a paste left a space on the
    front of it, and a URL that names Postgres should not quietly fall back
    to a local file because of whitespace.
    """
    return DATABASE_URL.strip().lower().startswith(("postgres://", "postgresql://"))


def q(sql: str) -> str:
    """The query as the driver at hand wants it.

    ``?`` becomes ``%s`` for Postgres and is left alone for SQLite. Written
    this way round because the query that goes over the wire is then the one
    that is in the code, and a reader comparing the two is looking at the same
    thing.
    """
    return sql if not using_postgres() else sql.replace("?", "%s")


class Result:
    """What the callers above this module ask of a finished statement."""

    def __init__(self, rows: List[Any], rowcount: int, lastrowid: Optional[int]) -> None:
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Any:
        # Any rather than Optional[Any]: every caller checks for None itself
        # before reading a column, and typing this as possibly-missing makes
        # each of those reads look unsafe when the check is right there.
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Any]:
        return list(self._rows)


class Database:
    """One connection's worth of statements, in either dialect."""

    def __init__(self, connection: Any, returner: Any) -> None:
        self._connection = connection
        # Takes the connection as its argument rather than closing a fixed
        # one, because this block may have replaced it already.
        self._returner = returner
        self._finished = False
        self._reconnected = False

    def _reset_connection(self) -> None:
        """Throw the dead connection away and take a fresh one.

        Anything this block had run so far is gone with it, which is why
        callers keep each block to statements that are safe to lose and see
        it through.
        """
        try:
            self._returner(self._connection)
        except Exception:
            # Returning a connection the server already killed is exactly
            # what the pool is for, and it discards a broken one itself.
            pass
        self._connection = _raw_connect()
        self._reconnected = True

    def _run(self, work: Any) -> Any:
        """Do a statement, and do it again on a fresh connection if the one
        in hand turns out to be dead.

        Neon closes connections from its side, and a connection can die
        between being handed out and being used - during a branch restart, or
        after a pause long enough for the far end to give up. A dead socket
        reads as a normal closed one until something is written to it, so
        this is only ever found out by trying. One retry is enough: a second
        failure means the database is actually unreachable, and pretending
        otherwise would turn a real outage into a slow one.
        """
        try:
            return work(self._connection)
        except transient_error() as exc:
            if self._reconnected:
                raise
            print(f"[DB] Connection was stale, reconnecting: {exc}".strip())
            self._reset_connection()
            return work(self._connection)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Result:
        def work(connection: Any) -> Result:
            cursor = connection.cursor()
            try:
                cursor.execute(q(sql), tuple(params))
                rows = _rows_from(cursor)
                return Result(
                    rows=rows,
                    rowcount=cursor.rowcount if cursor.rowcount is not None else 0,
                    lastrowid=None,
                )
            finally:
                cursor.close()

        return self._run(work)

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run an INSERT and return the id it made.

        SQLite reports that through the cursor and Postgres through RETURNING,
        so the same call site works on both without either spelling the other's
        idiom.
        """

        def work(connection: Any) -> int:
            if using_postgres():
                cursor = connection.cursor()
                try:
                    cursor.execute(q(sql) + " RETURNING id", tuple(params))
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("The insert returned no id.")
                    return int(row["id"] if not isinstance(row, tuple) else row[0])
                finally:
                    cursor.close()
            cursor = connection.cursor()
            try:
                cursor.execute(q(sql), tuple(params))
                return int(cursor.lastrowid or 0)
            finally:
                cursor.close()

        return self._run(work)

    def script(self, statements: str) -> None:
        """Several statements at once, for setting up tables.

        SQLite's driver has executescript for this; Postgres' does not, and
        splitting on the semicolon is what leaves it with something it can run.
        Only ever fed statements this module wrote, none of which carry a
        semicolon inside a string.
        """

        def work(connection: Any) -> None:
            if not using_postgres():
                connection.executescript(statements)
                return
            cursor = connection.cursor()
            try:
                for statement in _split_statements(statements):
                    cursor.execute(statement)
            finally:
                cursor.close()

        self._run(work)

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        if not self._finished:
            self._finished = True
            self._returner(self._connection)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        # Committed only when the block ran clean: a half-finished quota
        # update must not be kept. Closed either way - Neon caps how many
        # connections a branch may hold, so leaking one here is how a
        # service runs out.
        try:
            if exc_type is None:
                self.commit()
        finally:
            self.close()


def _split_statements(statements: str) -> Iterable[str]:
    for chunk in statements.split(";"):
        if chunk.strip():
            yield chunk.strip()


def _rows_from(cursor: Any) -> List[Any]:
    """Rows as mappings readable by column name, whichever driver produced them.

    Postgres is configured to hand back dicts (see connect), so its rows are
    passed straight on. sqlite3 hands back its own Row objects, which are
    readable by name but are not dicts, so they are converted - callers treat
    them as plain mappings and should not have to know which engine produced
    one.
    """
    if cursor.description is None:
        return []
    fetched: List[Any] = list(cursor.fetchall() or [])
    if using_postgres():
        return fetched
    return [dict(row) for row in fetched]


# One pool for the process. Opened on first use so that importing this module
# - which every request does - never opens a network connection by itself.
_pool: Optional[Any] = None
_pool_lock = threading.Lock()


def _close_pool() -> None:
    """Shut the pool down when the process ends.

    Without this the pool's worker threads are still waiting when the
    interpreter goes, and every run ends in a page of "couldn't stop thread"
    warnings - which is what makes a real connection problem easy to miss.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(_close_pool)


def _postgres_pool() -> Any:
    global _pool
    with _pool_lock:
        if _pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as exc:  # pragma: no cover - depends on install
                raise RuntimeError(
                    "DATABASE_URL points at Postgres but psycopg is not installed."
                    " Run: poetry add 'psycopg[binary,pool]'"
                ) from exc
            # Neon terminates connections that sit idle, and it does it from
            # the far end: the socket here still looks open, so the pool
            # would happily hand out a connection the server had already
            # closed and the first query after a quiet spell would fail with
            # "SSL connection has been closed unexpectedly".
            #
            # max_idle closes them on our side before that can happen, and
            # the check refuses to hand out one that is already dead. The
            # cost is an occasional reconnect, which is nothing next to a
            # failed signup or a clone that dies halfway.
            _pool = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=5,
                open=True,
                timeout=10.0,
                # Seconds a connection may sit unused before it is dropped
                # instead of reused. Well under Neon's own idle timeout.
                max_idle=30.0,
                check=ConnectionPool.check_connection,
            )
        return _pool


def _raw_connect() -> Any:
    """A connection with rows readable by name, and no Database around it.

    Split out so that reconnecting after a stale connection goes through the
    same door as opening the first one - a connection that came from anywhere
    else would come back as tuples.
    """
    if using_postgres():
        from psycopg.rows import dict_row

        pool = _postgres_pool()
        connection = pool.getconn()
        connection.row_factory = dict_row
        return connection

    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DEFAULT_SQLITE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _return_to_pool(connection: Any) -> None:
    # putconn checks the connection before reusing it and drops one that is
    # broken, so a socket the server killed does not go back into rotation.
    _postgres_pool().putconn(connection)


def _close_file(connection: Any) -> None:
    connection.close()


def connect() -> Database:
    """A connection to whichever database is configured.

    Used as a block, which commits and then closes::

        with db.connect() as db:
            db.execute(...)
    """
    if using_postgres():
        return Database(_raw_connect(), _return_to_pool)
    return Database(_raw_connect(), _close_file)


def transient_error() -> Tuple[Type[BaseException], ...]:
    """The errors worth trying again on a new connection.

    Empty on SQLite, which has no network to lose.
    """
    if not using_postgres():
        return ()
    from psycopg import InterfaceError, OperationalError

    return (OperationalError, InterfaceError)


def integrity_error() -> Type[BaseException]:
    """The error a broken constraint raises, whichever engine is in use.

    Used to turn "someone took that email first" into a sentence about
    accounts instead of a 500.
    """
    if using_postgres():
        from psycopg.errors import UniqueViolation

        return UniqueViolation
    return sqlite3.IntegrityError


def describe() -> str:
    """Where the data is going, for the startup line. Never the password."""
    if not using_postgres():
        return f"sqlite {DEFAULT_SQLITE_PATH}"
    # Host and database only: a Neon URL carries the password in it, and a
    # startup line is somewhere it ends up in a screenshot or a log paste.
    without_scheme = DATABASE_URL.split("://", 1)[-1]
    credentials, _, remainder = without_scheme.partition("@")
    host = remainder.split("/", 1)[0].split("?", 1)[0]
    return f"postgres {host} (user {credentials.split(':', 1)[0]})"