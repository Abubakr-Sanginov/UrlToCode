import ast
import builtins
import sys
import types
from pathlib import Path
from typing import List

import pytest

import clone_backend

SCHEMA = """
-- the entities this site's pages imply
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT,
    views INTEGER DEFAULT 0,
    published_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    slug TEXT UNIQUE
);

CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


# --- reading a generated schema ---------------------------------------------


def test_a_simple_schema_yields_its_tables():
    tables = clone_backend.parse_schema(SCHEMA)

    assert [table.name for table in tables] == ["posts", "comments"]


def test_a_column_type_maps_to_something_python_can_hold():
    posts = clone_backend.parse_schema(SCHEMA)[0]

    types = {column.name: column.python_type for column in posts.columns}
    assert types == {
        "id": "int",
        "title": "str",
        "body": "str",
        "views": "int",
        "published_at": "str",
        "slug": "str",
    }


def test_a_column_with_a_precision_is_one_column_not_two():
    # DECIMAL(10, 2) contains a comma, and splitting on it would produce two
    # broken columns and a server that does not start.
    tables = clone_backend.parse_schema("CREATE TABLE t (id INTEGER, price DECIMAL(10, 2));")

    assert [column.name for column in tables[0].columns] == ["id", "price"]


def test_table_constraints_are_not_mistaken_for_columns():
    tables = clone_backend.parse_schema(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            total DECIMAL(8, 2),
            CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id),
            UNIQUE (total)
        );
        """
    )

    assert [column.name for column in tables[0].columns] == ["id", "total"]


def test_a_commented_out_table_does_not_become_a_live_one():
    tables = clone_backend.parse_schema(
        """
        -- CREATE TABLE ghost (id INTEGER, name TEXT);
        CREATE TABLE real (id INTEGER, name TEXT);
        """
    )

    assert [table.name for table in tables] == ["real"]


def test_a_table_with_no_usable_columns_is_dropped():
    assert clone_backend.parse_schema("CREATE TABLE empty (PRIMARY KEY (a));") == []


def test_a_table_named_like_an_injection_is_refused():
    assert clone_backend.parse_schema('CREATE TABLE "a; DROP TABLE users" (id INT);') == []


def test_an_empty_schema_yields_nothing():
    assert clone_backend.parse_schema("") == []
    assert clone_backend.parse_schema("no tables here, just prose") == []


def test_not_null_and_unique_are_noticed():
    posts = clone_backend.parse_schema(SCHEMA)[0]
    by_name = {column.name: column for column in posts.columns}

    assert by_name["title"].nullable is False
    assert by_name["body"].nullable is True
    assert by_name["slug"].unique is True


def test_a_row_is_addressed_by_its_primary_key():
    posts = clone_backend.parse_schema(SCHEMA)[0]

    assert posts.key is not None
    assert posts.key.name == "id"


def test_a_table_with_no_declared_key_still_works():
    tables = clone_backend.parse_schema("CREATE TABLE t (name TEXT, note TEXT);")

    assert tables[0].key is not None
    assert tables[0].key.name == "name"


# --- building the project ---------------------------------------------------


def test_a_schema_with_no_tables_produces_no_server():
    # A server with no tables only serves static files, which the clone
    # already can do; shipping it would be noise.
    assert clone_backend.build_server("", ["/"]) == {}


def test_a_real_schema_produces_a_runnable_project():
    files = clone_backend.build_server(SCHEMA, ["/", "/about"], "Example")

    assert set(files) == {
        "server/app.py",
        "server/requirements.txt",
        "server/README.md",
        "server/.gitignore",
    }
    assert "uvicorn" in files["server/requirements.txt"]


def test_every_table_gets_its_crud():
    app = clone_backend.build_server(SCHEMA, ["/"])["server/app.py"]

    for path in ("/api/posts", "/api/comments"):
        assert f'@app.get("{path}")' in app
        assert f'@app.post("{path}", status_code=201)' in app
    assert '@app.get("/api/posts/{item_id}")' in app
    assert '@app.delete("/api/posts/{item_id}", status_code=204)' in app
    assert '@app.patch("/api/comments/{item_id}")' in app


def test_a_path_parameter_and_its_argument_have_the_same_name():
    # FastAPI binds a path parameter to the argument of the same name. A
    # route that says {id} and a function that takes item_id parses fine,
    # imports fine, and then refuses every request with a 422.
    app = clone_backend.build_server(SCHEMA, ["/"])["server/app.py"]
    tree = ast.parse(app)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            route = decorator.args[0]
            if not isinstance(route, ast.Constant) or "{" not in str(route.value):
                continue
            names = [
                part.split("}")[0].lstrip("{")
                for part in str(route.value).split("/")
                if "{" in part
            ]
            arguments = {argument.arg for argument in node.args.args}
            assert set(names) <= arguments, (
                f"{node.name} serves {route.value} but takes {sorted(arguments)}"
            )


def test_the_database_file_stays_out_of_version_control():
    # A SQLite file holds whatever the user typed into the clone, including
    # anything personal, and it is a build artefact rather than source. The
    # README is where the user is told where their data lives.
    files = clone_backend.build_server(SCHEMA, ["/"])

    assert "clone.db" in files["server/.gitignore"]
    assert "clone.db" in files["server/README.md"]


def test_a_site_name_cannot_break_the_generated_source():
    # The name comes from the site's own markup, so it can contain anything -
    # including the quotes that would close the strings it is written into.
    for name in ('The "Best" Site', "evil\n; import os", 'x"""', "{not_a_field}"):
        app = clone_backend.build_server(SCHEMA, ["/"], name)["server/app.py"]
        ast.parse(app)


def test_a_site_name_keeps_its_punctuation_in_the_title():
    app = clone_backend.build_server(SCHEMA, ["/"], 'The "Best" Site')["server/app.py"]

    assert "The \"Best\" Site" in app


def test_the_readme_says_how_to_start_it():
    readme = clone_backend.build_server(SCHEMA, ["/"], "Example")["server/README.md"]

    assert "pip install -r requirements.txt" in readme
    assert "uvicorn" in readme
    assert "posts" in readme
    assert "clone.db" in readme


# --- the generated file is real code ---------------------------------------


def test_the_generated_server_is_valid_python():
    app = clone_backend.build_server(SCHEMA, ["/"])["server/app.py"]

    ast.parse(app)


def test_the_generated_server_only_names_things_it_defines():
    # A generated file that calls a helper it never defined imports fine and
    # fails on the first request, which is the worst way for it to fail.
    app = clone_backend.build_server(SCHEMA, ["/"])["server/app.py"]
    tree = ast.parse(app)

    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    available = set(dir(builtins)) | set(dir(types)) | defined | imported
    missing = {name for name in called if name not in available}

    assert missing == set()


def test_the_tables_literal_matches_the_schema():
    app = clone_backend.build_server(SCHEMA, ["/"])["server/app.py"]

    tables = clone_backend.parse_schema(SCHEMA)
    for table in tables:
        assert f'"{table.slug}": {{"name": "{table.name}"' in app
        assert f"CREATE TABLE IF NOT EXISTS {table.name}" in app


def test_the_key_column_is_taken_from_the_table_not_assumed():
    app = clone_backend.build_server("CREATE TABLE t (slug TEXT PRIMARY KEY, title TEXT);", ["/"])[
        "server/app.py"
    ]

    assert '@app.get("/api/t/{item_id}")' in app
    assert "WHERE slug = ?" in app
    assert 'def get_t(item_id: str):' in app
