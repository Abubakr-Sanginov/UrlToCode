from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from routes.local_project import (
    LocalProjectFile,
    LocalProjectRequest,
    _safe_relative_parts,
    save_local_project,
    serve_generated_file,
)


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    response = await save_local_project(request)

    root = tmp_path / response.runId
    assert (root / "index.html").read_text(encoding="utf-8") == "<html></html>"
    assert (root / "about.html").exists()
    assert (root / "assets" / "image-1.svg").exists()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "My Site" in readme
    assert response.url == f"/generated/{response.runId}/"


@pytest.mark.asyncio
async def test_save_local_project_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="evil",
        files=[LocalProjectFile(path="../escape.txt", content="nope")],
    )

    with pytest.raises(HTTPException) as exc_info:
        await save_local_project(request)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_serve_generated_file_serves_entry_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("routes.local_project.GENERATED_SITES_DIR", tmp_path)

    request = LocalProjectRequest(
        siteName="demo",
        files=[LocalProjectFile(path="index.html", content="<html>demo</html>")],
    )
    response = await save_local_project(request)

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


def test_generated_sites_dir_is_ignored_by_git() -> None:
    gitignore = (
        Path(__file__).resolve().parent.parent / ".gitignore"
    ).read_text(encoding="utf-8")
    assert "generated_sites/" in gitignore
