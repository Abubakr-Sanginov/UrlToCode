"""The generated server, actually run.

The unit tests check that the generated file parses and that its names line
up. This one imports it, calls it, and reads a row back - because a server
that imports and then fails on the first request is exactly what a
hand-written-looking generated file turns out to be.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import pytest
from fastapi.testclient import TestClient

import clone_backend

SCHEMA = """
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT,
    views INTEGER DEFAULT 0
);
"""


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    """The generated app, in a folder of its own with a throwaway database."""
    files = clone_backend.build_server(SCHEMA, [], "Example")
    folder = tmp_path / "project"
    for name, content in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    monkeypatch.syspath_prepend(str(folder / "server"))
    for module in [name for name in sys.modules if name == "app"]:
        del sys.modules[module]

    spec = importlib.util.spec_from_file_location("generated_clone_app", folder / "server" / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with TestClient(module.app) as client:
        yield {"app": module, "client": client}


def test_the_tables_exist_after_startup(server: Dict[str, Any]):
    response = server["client"].get("/api/posts")

    assert response.status_code == 200
    assert response.json() == []


def test_a_row_can_be_created_and_read_back(server: Dict[str, Any]):
    client = server["client"]

    created = client.post("/api/posts", json={"title": "Hello", "body": "World"})

    assert created.status_code == 201
    row = created.json()
    assert row["title"] == "Hello"
    assert row["body"] == "World"
    assert row["id"] is not None

    fetched = client.get(f"/api/posts/{row['id']}")
    assert fetched.json() == row


def test_a_created_row_shows_up_in_the_list(server: Dict[str, Any]):
    client = server["client"]
    client.post("/api/posts", json={"title": "First"})

    rows = client.get("/api/posts").json()

    assert [row["title"] for row in rows] == ["First"]


def test_a_row_can_be_edited(server: Dict[str, Any]):
    client = server["client"]
    row = client.post("/api/posts", json={"title": "Before"}).json()

    updated = client.patch(f"/api/posts/{row['id']}", json={"title": "After"})

    assert updated.json()["title"] == "After"
    assert client.get(f"/api/posts/{row['id']}").json()["title"] == "After"


def test_a_row_can_be_deleted(server: Dict[str, Any]):
    client = server["client"]
    row = client.post("/api/posts", json={"title": "Gone"}).json()

    deleted = client.delete(f"/api/posts/{row['id']}")

    assert deleted.status_code == 204
    assert client.get(f"/api/posts/{row['id']}").status_code == 404


def test_an_unknown_row_is_a_404_and_not_a_crash(server: Dict[str, Any]):
    assert server["client"].get("/api/posts/9999").status_code == 404
    assert server["client"].delete("/api/posts/9999").status_code == 204


def test_a_missing_required_field_is_refused(server: Dict[str, Any]):
    # `title` is NOT NULL in the schema, so an empty post is a bad request
    # rather than a row the database silently accepts.
    response = server["client"].post("/api/posts", json={"body": "no title"})

    assert response.status_code == 422


def test_a_field_the_table_does_not_have_is_dropped(server: Dict[str, Any]):
    # Whatever a request carries, only the table's own columns are written.
    response = server["client"].post(
        "/api/posts", json={"title": "Fine", "nonsense": "ignored"}
    )

    assert response.status_code == 201
    assert "nonsense" not in response.json()


def test_a_numeric_field_given_as_text_is_still_stored_as_a_number(server: Dict[str, Any]):
    # A form posts strings; a view field that ends up as the text "12" sorts
    # and sums wrongly, so it is coerced on the way in.
    response = server["client"].post("/api/posts", json={"title": "T", "views": "12"})

    assert response.json()["views"] == 12


def test_the_list_can_be_paged(server: Dict[str, Any]):
    client = server["client"]
    for index in range(5):
        client.post("/api/posts", json={"title": f"Post {index}"})

    first_page = client.get("/api/posts?limit=2").json()

    assert len(first_page) == 2
    assert len(client.get("/api/posts?limit=2&offset=4").json()) == 1


def test_data_survives_a_reconnect(server: Dict[str, Any]):
    # The connection is per request; the file is what persists.
    server["client"].post("/api/posts", json={"title": "Kept"})

    rows = server["client"].get("/api/posts").json()

    assert rows[0]["title"] == "Kept"


def test_the_api_documentation_is_reachable(server: Dict[str, Any]):
    # FastAPI's own docs are where a user finds out what the API is.
    assert server["client"].get("/openapi.json").status_code == 200
