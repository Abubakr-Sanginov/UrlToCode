from pathlib import Path
from typing import Any, Dict

import pytest

import crawler.media_store as media_store
import routes.local_project as local_project
from crawler.media_store import (
    find_media,
    media_filename,
    media_path,
    rewrite_media_urls,
    save_media,
)
from prompts.html_compact import compact_html
from prompts.url_to_code_prompts import build_page_prompt


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(media_store, "MEDIA_DIR", tmp_path)
    # Defaults were bound at import time; point them at the temp store too.
    monkeypatch.setattr(
        local_project, "find_media", lambda url: find_media(url, tmp_path)
    )
    monkeypatch.setattr(
        local_project, "media_path", lambda name: media_path(name, tmp_path)
    )
    return tmp_path


def test_save_media_names_by_url_and_type(tmp_path: Path) -> None:
    url = "https://cdn.example.com/hero?w=800"
    name = save_media(url, "image/webp", b"RIFF", tmp_path)

    assert name == media_filename(url, "image/webp")
    assert name and name.endswith(".webp")
    assert find_media(url, tmp_path) == name
    assert media_path(name, tmp_path) == tmp_path / name


def test_save_media_refuses_non_media_and_oversize(tmp_path: Path) -> None:
    assert save_media("https://x.com/a.js", "text/javascript", b"x", tmp_path) is None
    too_big = b"x" * (media_store.MAX_IMAGE_BYTES + 1)
    assert save_media("https://x.com/a.png", "image/png", too_big, tmp_path) is None


def test_media_path_rejects_traversal(tmp_path: Path) -> None:
    assert media_path("../secret.txt", tmp_path) is None
    assert media_path("0123456789abcdef0123.png/..", tmp_path) is None


def test_rewrite_media_urls_handles_escaped_ampersands() -> None:
    media = {"https://x.com/a.jpg?w=1&h=2": "0123456789abcdef0123.jpg"}
    code = '<img src="https://x.com/a.jpg?w=1&amp;h=2">'

    rewritten = rewrite_media_urls(code, media, "http://127.0.0.1:7001")

    assert rewritten == '<img src="http://127.0.0.1:7001/crawl-assets/0123456789abcdef0123.jpg">'


def test_compactor_keeps_video_and_background_images() -> None:
    structure = compact_html(
        '<section data-bg-image="https://x.com/bg.jpg"><div data-bg-image="https://x.com/c.png"></div>'
        '<video src="https://x.com/v.mp4" poster="https://x.com/p.jpg" autoplay muted loop></video>'
        "</section>",
        max_chars=2000,
    )

    assert 'data-bg-image="https://x.com/bg.jpg"' in structure
    assert 'data-bg-image="https://x.com/c.png"' in structure
    assert 'src="https://x.com/v.mp4"' in structure
    assert 'poster="https://x.com/p.jpg"' in structure


def test_page_prompt_lists_videos() -> None:
    page: Dict[str, Any] = {
        "url": "https://x.com/",
        "path": "/",
        "html": "<p>hi</p>",
        "videos": [{"src": "https://x.com/v.mp4", "poster": "https://x.com/p.jpg"}],
    }
    prompt = build_page_prompt(page, "html_tailwind")[0]["content"]

    assert "VIDEOS" in prompt
    assert "src=https://x.com/v.mp4 poster=https://x.com/p.jpg" in prompt


def test_localize_static_site_media(store: Path) -> None:
    served = save_media("https://x.com/a.png", "image/png", b"png", store)
    original = save_media("https://x.com/b.jpg?x=1&y=2", "image/jpeg", b"jpg", store)

    changed, assets = local_project.localize_project_media(
        {
            "index.html": f'<img src="http://127.0.0.1:7001/crawl-assets/{served}">',
            "blog/post.html": '<div style="background-image:url(&quot;https://x.com/b.jpg?x=1&amp;y=2&quot;)"></div>',
            "README.md": "https://x.com/a.png",
        }
    )

    assert changed["index.html"] == f'<img src="assets/{served}">'
    assert f"url(&quot;../assets/{original}&quot;)" in changed["blog/post.html"]
    assert "README.md" not in changed
    assert assets == {f"assets/{served}": served, f"assets/{original}": original}


def test_localize_framework_media_uses_public_assets(store: Path) -> None:
    name = save_media("https://x.com/v.mp4", "video/mp4", b"mp4", store)
    served = f"http://127.0.0.1:7001/crawl-assets/{name}"

    changed, assets = local_project.localize_project_media(
        {
            "package.json": "{}",
            "app/about/page.tsx": f'<video src="{served}" />',
            "preview/about.html": f'<video src="{served}"></video>',
        }
    )

    assert changed["app/about/page.tsx"] == f'<video src="/assets/{name}" />'
    assert changed["preview/about.html"] == f'<video src="../public/assets/{name}"></video>'
    assert assets == {f"public/assets/{name}": name}


def test_page_prompt_carries_design_and_head() -> None:
    page: Dict[str, Any] = {
        "url": "https://x.com/",
        "path": "/",
        "html": "<p>hi</p>",
        "design": {
            "bodyFont": "Inter",
            "baseFontSize": "16px",
            "backgroundColor": "#ffffff",
            "buttonColor": "#2563eb",
            "palette": ["#111111", "#2563eb"],
            "fontLinks": ["https://fonts.googleapis.com/css2?family=Inter"],
        },
        "meta": {"lang": "ru", "favicon": "https://x.com/favicon.ico", "description": "D"},
    }

    html_prompt = build_page_prompt(page, "html_tailwind")[0]["content"]
    next_prompt = build_page_prompt(page, "nextjs_tailwind")[0]["content"]

    assert "Body font: Inter at 16px" in html_prompt
    assert "Primary buttons #2563eb" in html_prompt
    assert '<link rel="icon" href="https://x.com/favicon.ico">' in html_prompt
    assert '<html lang="ru">' in html_prompt
    # A framework layout owns the head; the page only gets the design.
    assert "Body font: Inter" in next_prompt
    assert "HEAD (include" not in next_prompt


def test_project_zip_contains_every_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import zipfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(local_project, "GENERATED_SITES_DIR", tmp_path)
    root = tmp_path / "20260101-000000-abcd1234"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (root / "assets" / "a.png").write_bytes(b"png")

    app = FastAPI()
    app.include_router(local_project.router)
    client = TestClient(app)

    response = client.get("/api/local-project/20260101-000000-abcd1234/zip?name=My Site!")
    assert response.status_code == 200
    assert 'filename="My-Site.zip"' in response.headers["content-disposition"]
    archive = zipfile.ZipFile(tmp_path / "20260101-000000-abcd1234.zip")
    assert sorted(archive.namelist()) == ["assets/a.png", "index.html"]

    assert client.get("/api/local-project/..%2Fetc/zip").status_code in (400, 404)
    assert client.get("/api/local-project/missing-run/zip").status_code == 404
