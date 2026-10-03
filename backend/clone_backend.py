"""Turning a cloned site into one that actually runs.

A clone is pages: markup that looks right and does nothing. Submit its contact
form and the page reloads; add an item to a list and it is gone on refresh. The
site works, and nothing in it is a program.

This builds the missing half. The crawl already produces a SQL schema naming
the entities the site implies, and the cloned pages already contain the forms
that would write to them, so the server is generated from those two rather than
from another model call: a model that invents a schema and a model that
invents the CRUD that matches it disagree often enough that the result is code
that looks right and does not run. Generated here, it runs.

The result is a small FastAPI application over SQLite that serves the cloned
pages and answers `/api/<table>` for every table in the schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Only what a cloned site's own CRUD needs. Anything richer is a guess about a
# database the original may not even have.
SQL_TO_PYTHON = {
    "INTEGER": "int",
    "INT": "int",
    "BIGINT": "int",
    "SMALLINT": "int",
    "REAL": "float",
    "FLOAT": "float",
    "DOUBLE": "float",
    "NUMERIC": "float",
    "DECIMAL": "float",
    "BOOLEAN": "bool",
    "BOOL": "bool",
    "TEXT": "str",
    "VARCHAR": "str",
    "CHAR": "str",
    "DATE": "str",
    "DATETIME": "str",
    "TIMESTAMP": "str",
    "JSON": "str",
    "BLOB": "str",
}

_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`]?(?P<name>[\w.]+)[\"'`]?\s*\((?P<body>.*)\)\s*;?",
    re.I | re.S,
)
_COLUMN = re.compile(
    r"^\s*(?:[\"'`]?(?P<name>\w+)[\"'`]?\s+)?(?P<type>[A-Za-z]+)(?:\([^)]*\))?"
    r"(?P<rest>.*)$",
    re.S,
)
_PRIMARY_KEY = re.compile(r"PRIMARY\s+KEY", re.I)
_NOT_NULL = re.compile(r"NOT\s+NULL", re.I)
_UNIQUE = re.compile(r"UNIQUE", re.I)
_DEFAULT_NOW = re.compile(r"DEFAULT\s+(CURRENT_TIMESTAMP|now\(\))", re.I)

# A table name is turned into a URL segment and a Python name; anything that
# is not a plain word is refused rather than mangled into a path.
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Column:
    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    default_now: bool = False

    @property
    def python_type(self) -> str:
        return SQL_TO_PYTHON.get(self.sql_type.upper(), "str")

    @property
    def sqlite_type(self) -> str:
        return self.sql_type.upper()

    @property
    def json_type(self) -> str:
        return {
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "str": "string",
        }.get(self.python_type, "string")

    def to_json(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "type": self.json_type,
            "nullable": self.nullable,
        }


@dataclass
class Table:
    name: str
    columns: List[Column] = field(default_factory=list[Column])

    @property
    def slug(self) -> str:
        return self.name.lower()

    @property
    def key(self) -> Optional[Column]:
        """The column a row is addressed by."""
        for column in self.columns:
            if column.primary_key:
                return column
        return self.columns[0] if self.columns else None

    def to_json(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "slug": self.slug,
            "columns": [column.to_json() for column in self.columns],
        }


def python_type(sql_type: str) -> str:
    return SQL_TO_PYTHON.get(sql_type.upper(), "str")


def parse_schema(sql: str) -> List[Table]:
    """The tables in a generated CREATE TABLE script.

    The schema comes from a model, so it is parsed rather than trusted: a
    statement that is not a table is skipped, and a column definition that
    does not make sense is dropped rather than guessed at. A server built
    from a mis-read column is a server that does not start.
    """
    tables: List[Table] = []
    if not sql:
        return tables

    # Comments are stripped first: a commented-out table must not become a
    # live one, and splitting on ';' blindly would cut a definition in half.
    cleaned = re.sub(r"--[^\n]*", " ", sql)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.S)

    for statement in cleaned.split(";"):
        if "CREATE" not in statement.upper():
            continue
        match = _CREATE_TABLE.search(statement)
        if not match:
            continue
        name = match.group("name").split(".")[-1]
        if not _SAFE_NAME.match(name):
            continue
        table = Table(name=name, columns=_parse_columns(match.group("body")))
        if not table.columns:
            # A table with no usable columns is a sentence the model wrote
            # rather than a table; there is nothing to serve.
            continue
        tables.append(table)

    return tables


def _parse_columns(body: str) -> List[Column]:
    """The column definitions in a CREATE TABLE body.

    Split on commas at depth zero, because `DECIMAL(10, 2)` and a type with
    brackets in it are both one definition, not two.
    """
    columns: List[Column] = []
    depth = 0
    current: List[str] = []
    parts: List[str] = []
    for char in body:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))

    for part in parts:
        column = _parse_column(part)
        if column is not None:
            columns.append(column)
    return columns


def _parse_column(part: str) -> Optional[Column]:
    text = part.strip()
    if not text:
        return None
    # A table-level constraint is not a column: it has no type of its own.
    if _PRIMARY_KEY.search(text) and "PRIMARY KEY" in text.upper()[:20] and " " not in text.strip("()"):
        return None
    upper = text.upper()
    if upper.startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE (", "CONSTRAINT", "CHECK", "INDEX", "KEY ")):
        return None

    match = _COLUMN.match(text)
    if not match:
        return None
    name = match.group("name")
    sql_type = (match.group("type") or "").strip()
    rest = match.group("rest") or ""
    if not name or not sql_type:
        return None
    if not _SAFE_NAME.match(name):
        return None

    return Column(
        name=name,
        sql_type=sql_type,
        nullable=not _NOT_NULL.search(rest),
        primary_key=_PRIMARY_KEY.search(rest) is not None,
        unique=_UNIQUE.search(rest) is not None,
        default_now=_DEFAULT_NOW.search(rest) is not None,
    )


def build_server(
    schema_sql: str,
    pages: List[str],
    site_name: str = "the site",
) -> Dict[str, str]:
    """The files that make the clone run.

    Returns a map of project-relative path to file content. An empty map when
    the schema yielded no usable table: a server with no tables is a server
    that only serves static files, which the user can already do.
    """
    tables = parse_schema(schema_sql)
    if not tables:
        return {}

    return {
        "server/app.py": _app_source(tables, pages, site_name),
        "server/requirements.txt": REQUIREMENTS,
        "server/README.md": _readme(tables, site_name),
        "server/.gitignore": "clone.db\n__pycache__/\n.venv/\n",
    }


REQUIREMENTS = "fastapi>=0.110\nuvicorn[standard]>=0.27\n"


def _app_source(tables: List[Table], pages: List[str], site_name: str) -> str:
    """The server itself.

    Written out as one readable file rather than assembled from fragments,
    because the result is something the user is meant to read, edit and run:
    it should look like something a person wrote.
    """
    return f'''"""A small server for {_docstring_name(site_name)}.

Generated from the clone's own data model, so the forms and lists in the
pages have something real to talk to. Run it:

    pip install -r requirements.txt
    python -m uvicorn app:app --reload

It serves the cloned pages out of the parent folder and answers JSON for
every table below.
"""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
PAGES = HERE.parent
DB_PATH = HERE / "clone.db"

# The tables, as the server needs to know them: name, sqlite type, python
# type, and whether the column may be left out.
TABLES: Dict[str, Dict[str, Any]] = {{
{_table_literal(tables)}
}}

SCHEMA = """
{_schema_statements(tables)}
"""


def connect() -> sqlite3.Connection:
    """One connection per request, with rows as dictionaries."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_tables() -> None:
    """Create the tables, leaving anything already stored alone.

    The database file lives in this folder, so it survives restarts;
    recreating it on every start would throw away what the user entered.
    """
    with connect() as connection:
        connection.executescript(SCHEMA)


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    yield


app = FastAPI(title={site_name!r}, lifespan=lifespan)


def fetch_one(table: str, item_id: Any) -> Optional[Dict[str, Any]]:
    """One row, or None when there is no such row."""
    spec = TABLES[table]
    with connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {{spec['name']}} WHERE {{spec['key']}} = ?", (item_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def clean_values(table: str, body: Dict[str, Any], only_set: bool = False) -> Dict[str, Any]:
    """The fields of `body` that this table has, coerced to its column types.

    Anything else in the request is dropped: a field the table does not have
    is either a typo or an injection, and either way it does not belong in an
    INSERT.
    """
    spec = TABLES[table]
    if only_set:
        wanted = set(body)
    else:
        wanted = {{name for name, _ in spec["columns"]}}
    out: Dict[str, Any] = {{}}
    for column in spec["columns"]:
        name, python_type, nullable = column
        if spec["key"] == name or name not in body or (only_set and name not in wanted):
            continue
        value = body.get(name)
        if value is None and nullable:
            out[name] = None
            continue
        out[name] = coerce(value, python_type)
    return out


def coerce(value: Any, python_type: str) -> Any:
    """Best effort at the declared type, leaving a mismatch to the database."""
    if value is None:
        return None
    if python_type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if python_type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if python_type == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value)

{_models(tables)}

{_endpoints(tables)}

# --- the cloned pages -------------------------------------------------------

# Mounted last, so the API above wins: `/api/...` is never answered with an
# HTML page, however the paths happen to line up on disk.
if PAGES.is_dir():
    app.mount("/", StaticFiles(directory=str(PAGES), html=True), name="pages")
'''


def _docstring_name(site_name: str) -> str:
    """The site name, safe to put inside a triple-quoted docstring.

    A site's name comes from its own markup, so it can contain anything,
    including the triple quote that would end the docstring it sits in.
    """
    return site_name.replace("\\", "/").replace('"""', "'''").replace("\n", " ").strip() or "the site"


def _table_literal(tables: List[Table]) -> str:
    """The table description the server carries at runtime.

    Written as a literal rather than re-parsed at startup, so the server has
    exactly the columns the generator saw and cannot disagree with the
    CREATE TABLE statements above it.
    """
    entries: List[str] = []
    for table in tables:
        key = table.key
        if key is None:
            continue
        columns = ", ".join(
            f'("{column.name}", "{column.python_type}", {column.nullable})'
            for column in table.columns
        )
        entries.append(
            f'    "{table.slug}": {{"name": "{table.name}", "key": "{key.name}", "columns": [{columns}]}},'
        )
    return "\n".join(entries)


def _schema_statements(tables: List[Table]) -> str:
    lines: List[str] = []
    for table in tables:
        columns: List[str] = []
        for column in table.columns:
            piece = f"  {column.name} {column.sqlite_type}"
            if column.primary_key:
                piece += " PRIMARY KEY AUTOINCREMENT" if column.sqlite_type == "INTEGER" else " PRIMARY KEY"
            if not column.nullable:
                piece += " NOT NULL"
            if column.unique:
                piece += " UNIQUE"
            if column.default_now:
                piece += " DEFAULT CURRENT_TIMESTAMP"
            columns.append(piece)
        lines.append(f"CREATE TABLE IF NOT EXISTS {table.name} (\n" + ",\n".join(columns) + "\n);")
    return "\n".join(lines)


def _models(tables: List[Table]) -> str:
    """One request model per table, so a bad request is refused at the edge.

    Optional fields are what they are: a column the original marked NOT NULL
    and a primary key are both filled in by the server or the database, so
    they are not asked for in the request body.
    """
    blocks: List[str] = []
    for table in tables:
        key = table.key
        fields: List[str] = []
        for column in table.columns:
            if key is not None and column.name == key.name:
                continue
            python = column.python_type
            if column.default_now:
                fields.append(f"    {column.name}: Optional[str] = None")
                continue
            default = "None" if column.nullable else "..."
            fields.append(f"    {column.name}: {python} = {default}")
        blocks.append(
            f"class {table.name}In(BaseModel):\n    \"\"\"A row of {table.slug}.\"\"\"\n\n"
            + "\n".join(fields)
            + "\n"
        )
    return "\n\n".join(blocks)


def _endpoints(tables: List[Table]) -> str:
    """The CRUD for every table, written out plainly."""
    blocks: List[str] = []
    for table in tables:
        key = table.key
        if key is None:
            continue
        slug = table.slug
        name = table.name
        key_type = key.python_type
        model = f"{name}In"

        blocks.append(
            f'''@app.get("/api/{slug}")
def list_{slug}(limit: int = 100, offset: int = 0):
    """Rows, oldest first. Paged so a long list is not the whole answer."""
    with connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM {name} ORDER BY {key.name} LIMIT ? OFFSET ?",
            (max(1, min(limit, 1000)), max(0, offset)),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/{slug}/{{item_id}}")
def get_{slug}(item_id: {key_type}):
    row = fetch_one("{slug}", item_id)
    if row is None:
        raise HTTPException(404, "No such {slug}")
    return row


@app.post("/api/{slug}", status_code=201)
def create_{slug}(body: {model}):
    data = clean_values("{slug}", body.model_dump(exclude_unset=True), only_set=True)
    if not data:
        return {{}}
    columns = ", ".join(data)
    markers = ", ".join("?" for _ in data)
    with connect() as connection:
        cursor = connection.execute(
            f"INSERT INTO {name} ({{columns}}) VALUES ({{markers}})", list(data.values())
        )
    return fetch_one("{slug}", cursor.lastrowid) or {{}}


@app.patch("/api/{slug}/{{item_id}}")
def update_{slug}(item_id: {key_type}, body: {model}):
    data = clean_values("{slug}", body.model_dump(exclude_unset=True), only_set=True)
    if not data:
        row = fetch_one("{slug}", item_id)
        if row is None:
            raise HTTPException(404, "No such {slug}")
        return row
    assignments = ", ".join(f"{{column}} = ?" for column in data)
    with connect() as connection:
        connection.execute(
            f"UPDATE {name} SET {{assignments}} WHERE {key.name} = ?",
            list(data.values()) + [item_id],
        )
    row = fetch_one("{slug}", item_id)
    if row is None:
        raise HTTPException(404, "No such {slug}")
    return row


@app.delete("/api/{slug}/{{item_id}}", status_code=204)
def delete_{slug}(item_id: {key_type}):
    with connect() as connection:
        connection.execute(f"DELETE FROM {name} WHERE {key.name} = ?", (item_id,))
    return None'''
        )

    return "\n\n\n".join(blocks)


def _readme(tables: List[Table], site_name: str) -> str:
    lines = [
        f"# Running {site_name}",
        "",
        "This folder is the backend for the cloned pages. It was generated from",
        "the site's own data model, so the forms and lists in the pages have",
        "something real to talk to.",
        "",
        "## Start it",
        "",
        "```",
        "pip install -r requirements.txt",
        "python -m uvicorn app:app --reload",
        "```",
        "",
        "The cloned pages are served from the parent folder, and the API is at",
        "`/docs` while the server is running.",
        "",
        "## The data",
        "",
        "Everything is stored in `clone.db`, a SQLite file next to `app.py`. It",
        "is created on first run and kept across restarts, so anything entered",
        "is still there tomorrow. Delete the file to start over.",
        "",
        "## The API",
        "",
    ]
    for table in tables:
        columns = ", ".join(f"{column.name} ({column.json_type})" for column in table.columns)
        lines.append(f"- `{table.slug}` - {columns}")
    lines.append("")
    lines.append("Each one answers:")
    lines.append("")
    lines.append("| Method | Path | Does |")
    lines.append("| --- | --- | --- |")
    lines.append("| GET | `/api/<table>` | list rows, `?limit=` and `?offset=` |")
    lines.append("| GET | `/api/<table>/<id>` | one row |")
    lines.append("| POST | `/api/<table>` | create a row |")
    lines.append("| PATCH | `/api/<table>/<id>` | change some fields |")
    lines.append("| DELETE | `/api/<table>/<id>` | delete a row |")
    lines.append("")
    return "\n".join(lines)
