"""The library of saved clones, and what a host needs to serve one.

The library exists because a generated site outlives the browser tab that
made it. What it must not do is pretend: listing something that cannot be
opened, or shipping a deploy config that breaks the project it is attached
to.
"""

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

from routes import local_project


@pytest.fixture(autouse=True)
def sites_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own place to save, never the developer's."""
    folder = tmp_path / "generated_sites"
    folder.mkdir()
    monkeypatch.setattr(local_project, "GENERATED_SITES_DIR", folder)
    return folder


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = pytest.importorskip("fastapi")
    from main import app as main_app

    with TestClient(main_app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def signed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project belongs to whoever saved it, so every test needs one.

    Autouse because the library is what an account's projects look like:
    a test that saved without one was not testing a library any user can
    reach.
    """
    import time

    import accounts as accounts_module
    from routes.accounts import _sign

    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    account = accounts_module.register("library@test.dev", "correct horse battery")
    # Room for as many clones as these tests save.
    accounts_module.set_tier(account.id, "studio")


@pytest.fixture(autouse=True)
def sessions_are_shared(signed_in: None, client: TestClient) -> None:
    """Attach the account to the client the tests save through."""
    import time

    import accounts as accounts_module
    from routes.accounts import SESSION_COOKIE, _sign

    account = accounts_module.account_by_id(1)
    assert account is not None
    client.cookies.set(SESSION_COOKIE, _sign(account.id, time.time()))


def save(client: TestClient, name: str = "Shop", source: str = "https://shop.test", **extra):
    return client.post(
        "/api/local-project/save",
        json={
            "siteName": name,
            "sourceUrl": source,
            "files": [
                {"path": "index.html", "content": "<html><body>home</body></html>"},
                {"path": "about.html", "content": "<html><body>about</body></html>"},
            ],
            **extra,
        },
    )


# --- the library -------------------------------------------------------------


def test_a_saved_clone_shows_up_in_the_library(client: TestClient) -> None:
    run_id = save(client).json()["runId"]

    entries = client.get("/api/local-project").json()["clones"]

    assert [entry["runId"] for entry in entries] == [run_id]
    assert entries[0]["name"] == "Shop"


def test_the_library_says_what_a_clone_was_a_clone_of(client: TestClient) -> None:
    # A folder of HTML with no provenance is an archive, not a library.
    save(client, source="https://shop.test")

    entry = client.get("/api/local-project").json()["clones"][0]

    assert entry["sourceUrl"] == "https://shop.test"
    assert entry["url"].startswith("/generated/")


def test_the_library_counts_the_pages_it_holds(client: TestClient) -> None:
    save(client)

    assert client.get("/api/local-project").json()["clones"][0]["pageCount"] == 2


def test_a_clone_with_a_generated_server_says_so(client: TestClient) -> None:
    client.post(
        "/api/local-project/save",
        json={
            "siteName": "With API",
            "files": [
                {"path": "index.html", "content": "<html></html>"},
                {"path": "server/app.py", "content": "app = 1"},
            ],
        },
    )

    assert client.get("/api/local-project").json()["clones"][0]["hasServer"] is True


def test_the_newest_clone_is_listed_first(client: TestClient) -> None:
    first = save(client, name="First").json()["runId"]
    second = save(client, name="Second").json()["runId"]

    entries = client.get("/api/local-project").json()["clones"]

    assert [entry["runId"] for entry in entries] == [second, first]


def test_a_project_copied_in_by_hand_is_still_listed(client: TestClient, sites_dir: Path) -> None:
    # The folders are the storage, not the index file. A project someone
    # dropped in by hand is real, and hiding it would be a lie.
    (sites_dir / "by-hand").mkdir()
    (sites_dir / "by-hand" / "index.html").write_text("<html></html>")

    entries = client.get("/api/local-project").json()["clones"]

    assert [entry["runId"] for entry in entries] == ["by-hand"]
    assert entries[0]["name"] == "by-hand"


def test_a_folder_with_no_readable_metadata_does_not_break_the_listing(
    client: TestClient, sites_dir: Path
) -> None:
    broken = sites_dir / "broken"
    broken.mkdir()
    (broken / "site.json").write_text("{ not json", encoding="utf-8")

    assert client.get("/api/local-project").status_code == 200


def test_an_empty_library_is_an_empty_list_not_an_error(client: TestClient) -> None:
    assert client.get("/api/local-project").json() == {"clones": []}


# --- forgetting --------------------------------------------------------------


def test_a_clone_can_be_forgotten(client: TestClient) -> None:
    run_id = save(client).json()["runId"]

    assert client.delete(f"/api/local-project/{run_id}").json()["deleted"] is True
    assert client.get("/api/local-project").json()["clones"] == []


def test_forgetting_a_clone_deletes_its_files(client: TestClient, sites_dir: Path) -> None:
    run_id = save(client).json()["runId"]

    client.delete(f"/api/local-project/{run_id}")

    assert not (sites_dir / run_id).exists()


def test_forgetting_a_clone_nobody_saved_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/local-project/nope").status_code == 404


def test_forgetting_a_clone_outside_the_folder_is_refused(client: TestClient) -> None:
    assert client.delete("/api/local-project/..%2F..%2Fetc").status_code in (400, 404)


# --- the deploy bundle -------------------------------------------------------


def bundle_of(client: TestClient, run_id: str) -> Dict[str, str]:
    response = client.get(f"/api/local-project/{run_id}/deploy")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {
            name: archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
        }


def test_the_deploy_bundle_contains_the_project(client: TestClient) -> None:
    run_id = save(client).json()["runId"]

    files = bundle_of(client, run_id)

    assert "index.html" in files
    assert "about.html" in files


def test_a_multi_page_clone_is_not_rewritten_to_one_page(client: TestClient) -> None:
    # A rewrite sending every path to index.html would turn a working
    # multi-page site into one that shows the landing page forever.
    run_id = save(client).json()["runId"]

    assert "_redirects" not in bundle_of(client, run_id)


def test_a_framework_project_gets_the_spa_fallback(client: TestClient) -> None:
    client.post(
        "/api/local-project/save",
        json={
            "siteName": "Next",
            "files": [
                {"path": "index.html", "content": "<html></html>"},
                {"path": "package.json", "content": "{}"},
                {"path": "next.config.js", "content": ""},
                {"path": "app/page.tsx", "content": "export default () => null"},
            ],
        },
    )
    run_id = client.get("/api/local-project").json()["clones"][0]["runId"]

    files = bundle_of(client, run_id)

    # Without this, a refresh on /about is a 404 on the deployed site.
    assert "/*" in files["_redirects"]
    assert "index.html" in files["_redirects"]


def test_a_generated_server_ships_with_a_container_that_builds_it(
    client: TestClient,
) -> None:
    client.post(
        "/api/local-project/save",
        json={
            "siteName": "With API",
            "files": [
                {"path": "index.html", "content": "<html></html>"},
                {"path": "server/app.py", "content": "app = 1"},
                {"path": "server/requirements.txt", "content": "fastapi\n"},
            ],
        },
    )
    run_id = client.get("/api/local-project").json()["clones"][0]["runId"]

    files = bundle_of(client, run_id)

    assert "uvicorn" in files["Dockerfile"]
    assert "server/requirements.txt" in files["Dockerfile"]
    assert "services:" in files["render.yaml"]


def test_a_project_with_no_server_gets_no_container_file(client: TestClient) -> None:
    run_id = save(client).json()["runId"]

    assert "Dockerfile" not in bundle_of(client, run_id)


def test_a_deploy_bundle_for_nothing_is_a_404(client: TestClient) -> None:
    assert client.get("/api/local-project/missing/deploy").status_code == 404
