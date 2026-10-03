import html
import json
import posixpath
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from crawler.media_store import MEDIA_ROUTE, find_media, media_path
import accounts
import dev_server
from routes import accounts as accounts_route

router = APIRouter()

# Generated projects live under backend/generated_sites/. The directory is
# created on first save and reused for every run.
GENERATED_SITES_DIR = Path(__file__).resolve().parent.parent / "generated_sites"

# Entry file names a generated site might use, checked in priority order when
# deciding which file to serve for a directory request.
ENTRY_CANDIDATES = ["index.html", "portal.html"]

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
WINDOWS_RESERVED_RE = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE
)


class LocalProjectFile(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    # Generated pages are single HTML files; 5 MB per file is far above any
    # real output and stops runaway payloads from filling the disk.
    content: str = Field(max_length=5_000_000)


class LocalProjectRequest(BaseModel):
    siteName: str | None = None
    # Kept so the library can say what a saved clone was a clone *of*. A
    # folder of HTML files with no provenance is an archive, not a library.
    sourceUrl: str | None = Field(default=None, max_length=2000)
    files: List[LocalProjectFile] = Field(min_length=1, max_length=200)


class LibraryEntry(BaseModel):
    runId: str
    name: str
    sourceUrl: str
    savedAt: float
    pageCount: int
    hasServer: bool
    url: str
    sizeBytes: int


# Written next to the project so the library can be rebuilt from disk alone.
# The directory is the storage; this file is the index into it.
LIBRARY_FILE = "site.json"


class LocalProjectAsset(BaseModel):
    # Where the file goes in the project, and where the backend serves it.
    path: str
    url: str


class LocalProjectResponse(BaseModel):
    runId: str
    files: int
    url: str
    # Captured images/videos copied into the project, and the text files
    # whose media URLs now point at them. The frontend writes both into the
    # folder it saves to.
    assets: List[LocalProjectAsset] = Field(default_factory=list[LocalProjectAsset])
    rewritten: List[LocalProjectFile] = Field(default_factory=list[LocalProjectFile])
    # What this save left of the owner's allowance, so the UI can say so
    # without asking the server a second time.
    usage: Dict[str, Any] = Field(default_factory=dict[str, Any])


_MEDIA_URL_RE = re.compile(r"""https?://[^\s"'<>()\\]+""")
_SERVED_MEDIA_RE = re.compile(re.escape(MEDIA_ROUTE) + r"/([0-9a-f]{20}\.[a-z0-9]{1,5})$")


def _is_framework_project(paths: List[str]) -> bool:
    return "package.json" in paths and any(
        path.startswith(("app/", "src/")) for path in paths
    )


def _stored_media_name(url: str) -> str | None:
    """Store file behind a URL in generated code, if it is captured media."""
    served = _SERVED_MEDIA_RE.search(url)
    if served:
        name = served.group(1)
        return name if media_path(name) else None
    return find_media(html.unescape(url))


def localize_project_media(
    files: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Point captured media URLs at files inside the project.

    Returns the changed files and {project path: store file name} for every
    media file to copy in. A framework project keeps media in public/assets
    (served at /assets); a static site in assets/ next to its pages.
    """
    framework = _is_framework_project(list(files))
    assets_dir = "public/assets" if framework else "assets"

    changed: Dict[str, str] = {}
    assets: Dict[str, str] = {}
    for path, content in files.items():
        if not path.endswith((".html", ".htm", ".tsx", ".ts", ".jsx", ".js", ".css")):
            continue
        replacements: Dict[str, str] = {}
        for match in set(_MEDIA_URL_RE.findall(content)):
            # url(&quot;https://...&quot;) in a style attribute.
            url = re.sub(r"(&quot;|&#39;|&#x27;).*$", "", match)
            name = _stored_media_name(url)
            if not name:
                continue
            # Framework sources and Vite's index.html resolve /assets from public/.
            if framework and (path.startswith(("app/", "src/")) or path == "index.html"):
                replacements[url] = f"/assets/{name}"
            else:
                directory = posixpath.dirname(path) or "."
                replacements[url] = posixpath.relpath(f"{assets_dir}/{name}", directory)
            assets[f"{assets_dir}/{name}"] = name
        if not replacements:
            continue
        for url in sorted(replacements, key=len, reverse=True):
            content = content.replace(url, replacements[url])
        changed[path] = content
    return changed, assets


def _safe_relative_parts(raw_path: str) -> Path:
    """Turn an untrusted path into safe relative parts, refusing escapes."""
    cleaned = raw_path.strip().replace("\\", "/")
    # Strip a leading slash so "C:\evil" or "/abs" cannot anchor outside.
    while cleaned.startswith("/"):
        cleaned = cleaned[1:]
    if not cleaned:
        raise ValueError("Empty path")

    parts: List[str] = []
    for segment in cleaned.split("/"):
        segment = segment.strip()
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError("Path traversal is not allowed")
        if WINDOWS_RESERVED_RE.match(segment):
            segment = f"_{segment}"
        parts.append(segment)

    if not parts:
        raise ValueError("Empty path")

    return Path(*parts)


def _resolve_under(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path traversal is not allowed") from exc
    return candidate


def _project_root(run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise ValueError("Invalid project id")
    root = (GENERATED_SITES_DIR / run_id).resolve()
    try:
        root.relative_to(GENERATED_SITES_DIR)
    except ValueError as exc:
        raise ValueError("Invalid project id") from exc
    return root


def build_readme(site_name: str, file_names: List[str]) -> str:
    pages = "\n".join(f"- `{name}`" for name in file_names)
    return f"""# {site_name}

Generated by UrlToCode.

## Run locally

Open `index.html` in a browser, or serve the folder with any static server:

```bash
python -m http.server 8080
```

## Files

{pages}
"""


def _write_library_entry(
    root: Path,
    run_id: str,
    site_name: str,
    source_url: str,
    written: List[str],
) -> None:
    """Record what this clone is, so the library can list it later.

    Best effort: a project that cannot be indexed is still a working
    project, and refusing to save it because a metadata file failed would
    be a poor trade.
    """
    try:
        entry: Dict[str, Any] = {
            "runId": run_id,
            "name": site_name,
            "sourceUrl": source_url,
            "savedAt": time.time(),
            "pageCount": sum(1 for name in written if name.endswith((".html", ".htm"))),
            "hasServer": any(name.startswith("server/") for name in written),
        }
        (root / LIBRARY_FILE).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[LOCAL-PROJECT] Could not index {run_id}: {exc}")


def _read_library_entry(root: Path, run_id: str) -> Optional[LibraryEntry]:
    """One saved clone, or None when the folder cannot be listed at all.

    A project saved before this existed has no `site.json`; it is still
    listed, under its folder name, because hiding a working project is
    worse than showing it with less detail. Only a folder we cannot read
    gives up entirely.
    """
    try:
        raw: Any = json.loads((root / LIBRARY_FILE).read_text(encoding="utf-8"))
        data: Dict[str, Any] = cast(Dict[str, Any], raw) if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        data = {}
    try:
        saved_at: Any = data.get("savedAt")
        return LibraryEntry(
            runId=run_id,
            name=str(data.get("name") or root.name),
            sourceUrl=str(data.get("sourceUrl") or ""),
            savedAt=float(saved_at) if isinstance(saved_at, (int, float)) else 0.0,
            pageCount=int(data.get("pageCount") or 0),
            hasServer=bool(data.get("hasServer")),
            url=f"/generated/{run_id}/",
            sizeBytes=sum(f.stat().st_size for f in root.rglob("*") if f.is_file()),
        )
    except OSError:
        return None


@router.get("/api/local-project")
async def list_local_projects() -> Dict[str, Any]:
    """Every saved clone on this server, newest first.

    Read from the folders rather than from an index file, so a project that
    was copied in by hand is listed too, and a deleted folder disappears on
    its own.
    """
    entries: List[LibraryEntry] = []
    if GENERATED_SITES_DIR.is_dir():
        for child in GENERATED_SITES_DIR.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            entry = _read_library_entry(child, child.name)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda item: item.savedAt, reverse=True)
    return {"clones": [entry.model_dump() for entry in entries]}


@router.delete("/api/local-project/{run_id}")
async def delete_local_project(run_id: str) -> Dict[str, Any]:
    """Forget one saved clone and delete its files."""
    try:
        root = _project_root(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    # The name is validated, so this is a directory we created - but the
    # check is cheap and a mistake here is not recoverable.
    shutil.rmtree(root, ignore_errors=True)
    archive = GENERATED_SITES_DIR / f"{run_id}.zip"
    archive.unlink(missing_ok=True)
    return {"runId": run_id, "deleted": True}


# --- deploying ---------------------------------------------------------------

# Hosts that serve a static folder with no build step. Each needs a file of
# its own, and the two formats differ: Netlify reads `_redirects`, Cloudflare
# Pages reads `_headers` for caching and `_redirects` for rewrites.
_STATIC_REDIRECTS = "/*  /index.html  200\n"
_STATIC_HEADERS = "/*\n  X-Content-Type-Options: nosniff\n  Cache-Control: public, max-age=3600\n"


def _has_server(root: Path) -> bool:
    return (root / "server" / "app.py").is_file()


def _has_spa(root: Path) -> bool:
    """Whether the project needs every unknown path served as index.html.

    A multi-page clone has real files at real paths, and rewriting them all
    to index.html would break its navigation. A framework project is the
    opposite: its pages are routes, and without the fallback a refresh on
    /about is a 404.
    """
    return (root / "package.json").is_file() and (root / "next.config.js").is_file()


def build_deploy_files(root: Path) -> Dict[str, str]:
    """The handful of files a host needs to serve this project as-is.

    Generated from what the project actually contains rather than from the
    stack that was asked for, because a rewrite that sends every path to
    index.html turns a working multi-page site into one that shows the
    landing page forever.
    """
    files: Dict[str, str] = {}
    if _has_spa(root):
        files["_redirects"] = _STATIC_REDIRECTS
    files["_headers"] = _STATIC_HEADERS
    if _has_server(root):
        # The generated server is a FastAPI app with one dependency; a
        # Dockerfile is all a host that builds containers needs.
        files["Dockerfile"] = (
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY server/requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY server/ .\n"
            "EXPOSE 8000\n"
            'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]\n'
        )
        files["render.yaml"] = (
            "services:\n"
            "  - type: web\n"
            "    name: cloned-site-api\n"
            "    runtime: docker\n"
            "    plan: free\n"
        )
        files["api-index.json"] = "{}\n"
    return files


class DevFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=5_000_000)


@router.get("/api/local-project/{run_id}/dev")
async def dev_server_status(run_id: str) -> Dict[str, Any]:
    """Whether this project has a dev server running, and where it is."""
    server = dev_server.status(run_id)
    if server is None:
        return {"runId": run_id, "running": False, "url": "", "log": [], "error": ""}
    return server.to_json()


@router.post("/api/local-project/{run_id}/dev")
async def start_dev_server(run_id: str) -> Dict[str, Any]:
    """Start the project's own dev server and wait for it to answer.

    This runs model-generated code on the user's machine, which is why it
    is a separate, explicit action rather than something a save does behind
    the user's back. It never runs `npm install` - see `dev_server`.
    """
    try:
        server = await dev_server.start_and_wait(run_id)
    except dev_server.DevServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = server.to_json()
    if not server.running or server.error:
        payload["error"] = server.error or "The dev server did not start."
    return payload


@router.delete("/api/local-project/{run_id}/dev")
async def stop_dev_server(run_id: str) -> Dict[str, Any]:
    return {"runId": run_id, "stopped": dev_server.stop(run_id)}


@router.put("/api/local-project/{run_id}/dev/file")
async def write_dev_file(run_id: str, body: DevFileRequest) -> Dict[str, Any]:
    """Write one file into the running project so it hot-reloads.

    This is the whole reason the dev server exists: the edit lands on disk
    and the framework rebuilds the page, rather than the user reloading an
    iframe and losing their place.
    """
    try:
        written = dev_server.write_file(run_id, body.path, body.content)
    except dev_server.DevServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": written}


@router.get("/api/local-project/{run_id}/deploy")
async def download_deploy_bundle(run_id: str, name: str = "cloned-site") -> Response:
    """The project plus the config its host needs, as one ZIP.

    Not a deploy: it puts the project in a shape where dropping the folder
    on a static host, or pushing the repository to one that builds a
    container, is the only step left. Anything that called itself a
    deployment from here would be claiming a credential flow this does not
    have.
    """
    try:
        root = _project_root(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")

    extras = build_deploy_files(root)
    archive = GENERATED_SITES_DIR / f"{run_id}-deploy.zip"
    count = build_project_zip(root, archive)
    with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as bundle:
        for filename, content in extras.items():
            bundle.writestr(filename, content)
            count += 1
    safe = _ZIP_NAME_RE.sub("-", name).strip("-.")[:60] or "cloned-site"
    return FileResponse(
        archive, media_type="application/zip", filename=f"{safe}-deploy.zip"
    )


@router.post("/api/local-project/save")
async def save_local_project(
    request: LocalProjectRequest,
    utc_session: Optional[str] = Cookie(default=None),
) -> LocalProjectResponse:
    """Persist a generated site to disk and expose it on a local URL.

    The frontend writes the same files into a user-chosen folder via the File
    System Access API; this endpoint mirrors that payload so the site can be
    served immediately without a manual dev server.

    A project belongs to the account that saved it, and the free tier keeps
    one. That is checked before anything is written: the files are the
    expensive part, and refusing afterwards would leave a project on disk
    that the owner is not allowed to open.
    """
    account = accounts_route.require_account(utc_session)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    try:
        accounts.check_project_capacity(account, run_id)
    except accounts.NoCapacity as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    root = GENERATED_SITES_DIR / run_id
    written: List[str] = []
    try:
        for file in request.files:
            relative = _safe_relative_parts(file.path)
            target = _resolve_under(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.content, encoding="utf-8")
            written.append(str(relative).replace("\\", "/"))
    except (OSError, ValueError) as exc:
        # Don't leave a half-written project behind; it would keep being
        # served as if it were complete.
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    changed, assets = localize_project_media(
        {name: file.content for name, file in zip(written, request.files)}
    )
    for name, content in changed.items():
        (root / name).write_text(content, encoding="utf-8")
    copied: List[LocalProjectAsset] = []
    for asset_path, store_name in assets.items():
        source = media_path(store_name)
        if source is None:
            continue
        target = root / asset_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(
            LocalProjectAsset(path=asset_path, url=f"/generated/{run_id}/{asset_path}")
        )

    # A framework project ships its own README (npm install / npm run dev).
    if "README.md" not in written:
        readme_names = [name for name in written if not name.startswith("assets/")]
        (root / "README.md").write_text(
            build_readme(request.siteName or "Generated site", readme_names),
            encoding="utf-8",
        )

    _write_library_entry(
        root,
        run_id=run_id,
        site_name=request.siteName or "Generated site",
        source_url=request.sourceUrl or "",
        written=written,
    )

    usage = accounts.add_project(
        account, run_id, request.siteName or "Generated site", request.sourceUrl or ""
    )

    print(
        f"[LOCAL-PROJECT] Saved {len(written)} files and {len(copied)} media "
        f"files under {run_id}"
    )
    return LocalProjectResponse(
        runId=run_id,
        files=len(written) + len(copied),
        url=f"/generated/{run_id}/",
        assets=copied,
        usage={
            "used": usage.used,
            "remaining": usage.remaining,
            "projects": usage.projects,
            "maxProjects": usage.max_projects,
            "tier": usage.tier,
            "resetsAt": usage.resets_at,
        },
        rewritten=[
            LocalProjectFile(path=name, content=content)
            for name, content in changed.items()
        ],
    )


# Already-compressed media gains nothing from deflate; storing is faster.
_STORED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm", ".ogv", ".mov", ".ico"}
)
_ZIP_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def build_project_zip(root: Path, target: Path) -> int:
    """Pack every file under `root` into `target`; returns the file count."""
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            compress = (
                zipfile.ZIP_STORED
                if path.suffix.lower() in _STORED_EXTENSIONS
                else zipfile.ZIP_DEFLATED
            )
            archive.write(path, path.relative_to(root).as_posix(), compress_type=compress)
            count += 1
    return count


@router.get("/api/local-project/{run_id}/zip")
async def download_project_zip(run_id: str, name: str = "cloned-site"):
    """The saved project as one ZIP download, for any browser."""
    try:
        root = _project_root(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")

    archive = GENERATED_SITES_DIR / f"{run_id}.zip"
    if not archive.is_file():
        build_project_zip(root, archive)
    filename = _ZIP_NAME_RE.sub("-", name).strip("-.")[:60] or "cloned-site"
    return FileResponse(
        archive, media_type="application/zip", filename=f"{filename}.zip"
    )


@router.get(MEDIA_ROUTE + "/{name}")
async def serve_crawl_media(name: str):
    """Serve an image or video captured while crawling (Range-capable)."""
    path = media_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/generated/{run_id}/{file_path:path}")
async def serve_generated_file(run_id: str, file_path: str):
    """Serve a saved project over HTTP so it can be 'run' with one click."""
    try:
        root = _project_root(run_id)
        requested = file_path.rstrip("/") or "index.html"
        full_path = _resolve_under(root, _safe_relative_parts(requested))
    except ValueError:
        return Response(
            content=_error_page("Invalid project path"), media_type="text/html"
        )

    if full_path.is_dir():
        for candidate in ENTRY_CANDIDATES:
            entry = full_path / candidate
            if entry.is_file():
                return _sandboxed_file(entry, "text/html")
        return Response(
            content=_error_page("No index.html in this folder"),
            media_type="text/html",
        )

    if not full_path.is_file():
        return Response(
            content=_error_page("File not found in the generated project"),
            media_type="text/html",
        )

    return _sandboxed_file(full_path, None)


def _sandboxed_file(path: Path, media_type: str | None) -> Response:
    """Serve generated content cut off from the backend's origin.

    Generated HTML is model output of unknown trust; `CSP: sandbox` gives it
    its own opaque origin so it cannot script against the API routes.
    """
    headers = {"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups"}
    if media_type:
        return FileResponse(path, media_type=media_type, headers=headers)
    return FileResponse(path, headers=headers)


@router.get("/generated/{run_id}")
async def serve_generated_root(run_id: str):
    return await serve_generated_file(run_id, "index.html")


def _error_page(message: str) -> bytes:
    html = f"""<!DOCTYPE html>
<html>
  <body style="font-family: system-ui, sans-serif; padding: 3rem; color: #334;">
    <h1>UrlToCode</h1>
    <p>{message}</p>
  </body>
</html>"""
    return html.encode("utf-8")
