from pathlib import Path
from typing import Optional

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

import accounts as accounts_module
from routes.local_project import (
    LocalProjectFile,
    LocalProjectRequest,
    _safe_relative_parts,
    save_local_project,
    serve_generated_file,
)
from routes.accounts import _sign


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A signed-in caller.

    A project belongs to whoever saved it, so saving without an account is
    refused rather than filed under nobody.
    """
    import time

    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    account = accounts_module.register("owner@test.dev", "correct horse battery")
    return _sign(account.id, time.time())


def test_safe_relative_parts_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        _safe_relative_parts("../evil.txt")
    with pytest.raises(ValueError):
        _safe_relative_parts("a/../../evil.txt")
    with pytest.raises(ValueError):
        _safe_relative_parts("")


def test_safe_relative_parts_strips_anchors_and_backslashes() -> None:
    relative = _safe_relative_parts("\\assets\\img\\logo.png")
    assert str(relative).replace("\\", "/") == "assets/img/logo.png"


def test_safe_relative_parts_prefixes_windows_reserved_names() -> None:
    relative = _safe_relative_parts("con.html")
    assert str(relative).startswith("_")


@pytest.mark.asyncio
async def test_save_local_project_writes_files_and_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="My Site",
        files=[
            LocalProjectFile(path="index.html", content="<html></html>"),
            LocalProjectFile(path="about.html", content="<html></html>"),
            LocalProjectFile(path="assets/image-1.svg", content="<svg/>"),
        ],
    )

    response = await save_local_project(request, session)

    root = tmp_path / response.runId
    assert (root / "index.html").read_text(encoding="utf-8") == "<html></html>"
    assert (root / "about.html").exists()
    assert (root / "assets" / "image-1.svg").exists()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "My Site" in readme
    assert response.url == f"/generated/{response.runId}/"


@pytest.mark.asyncio
async def test_save_local_project_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="evil",
        files=[LocalProjectFile(path="../escape.txt", content="nope")],
    )

    with pytest.raises(HTTPException) as exc_info:
        await save_local_project(request, session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_serve_generated_file_serves_entry_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="demo",
        files=[LocalProjectFile(path="index.html", content="<html>demo</html>")],
    )
    response = await save_local_project(request, session)

    file_response = await serve_generated_file(response.runId, "index.html")
    assert isinstance(file_response, FileResponse)
    assert Path(file_response.path).name == "index.html"


@pytest.mark.asyncio
async def test_serve_generated_file_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    response = await serve_generated_file("somerunid", "../secret.txt")
    assert b"Invalid" in response.body


@pytest.mark.asyncio
async def test_a_second_project_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    """The limit is checked first, so a refusal leaves no project behind."""
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    def _request(name: str) -> LocalProjectRequest:
        return LocalProjectRequest(
            siteName=name,
            files=[LocalProjectFile(path="index.html", content="<html></html>")],
        )

    await save_local_project(_request("First"), session)

    with pytest.raises(HTTPException) as exc_info:
        await save_local_project(_request("Second"), session)

    assert exc_info.value.status_code == 402
    # One project directory, not two: the refused save left nothing to
    # clean up. Counted as directories because the test's own database
    # sits in the same folder.
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


@pytest.mark.asyncio
async def test_saving_without_an_account_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="anonymous",
        files=[LocalProjectFile(path="index.html", content="<html></html>")],
    )

    with pytest.raises(HTTPException) as exc_info:
        await save_local_project(request, None)
    assert exc_info.value.status_code == 401
    assert list(tmp_path.iterdir()) == []


def test_generated_sites_dir_is_ignored_by_git() -> None:
    gitignore = (
        Path(__file__).resolve().parent.parent / ".gitignore"
    ).read_text(encoding="utf-8")
    assert "generated_sites/" in gitignore
