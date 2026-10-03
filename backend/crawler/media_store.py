"""On-disk store for images and videos captured while crawling.

The crawler worker saves every media file the page loaded (straight from the
network responses) or referenced (a <video>, a lazy image) under
`crawl_assets/`, named by a hash of its original URL. Generation then points
the clone at the local copy, and saving a project copies the files it uses
next to its pages, so a clone keeps its pictures and videos after the source
site changes, expires signed URLs or blocks hotlinking.
"""

import hashlib
import re
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

MEDIA_DIR = Path(__file__).resolve().parent.parent / "crawl_assets"

# Route the backend serves the store on (routes/local_project.py).
MEDIA_ROUTE = "/crawl-assets"

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 80 * 1024 * 1024
# Per crawl, so a video-heavy site cannot fill the disk.
MAX_CRAWL_MEDIA_BYTES = 500 * 1024 * 1024

MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{20}\.[a-z0-9]{1,5}$")

_CONTENT_TYPE_EXT: Dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/svg+xml": "svg",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/bmp": "bmp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/ogg": "ogv",
    "video/quicktime": "mov",
}
_KNOWN_EXTS = frozenset(_CONTENT_TYPE_EXT.values()) | {"jpeg"}


def media_kind(content_type: str) -> Optional[str]:
    """"image" or "video" for a storable content type, else None."""
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype not in _CONTENT_TYPE_EXT:
        return None
    return "video" if ctype.startswith("video/") else "image"


def _extension(url: str, content_type: str) -> str:
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype in _CONTENT_TYPE_EXT:
        return _CONTENT_TYPE_EXT[ctype]
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix in _KNOWN_EXTS:
        return "jpg" if suffix == "jpeg" else suffix
    return "bin"


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def media_filename(url: str, content_type: str) -> str:
    return f"{url_key(url)}.{_extension(url, content_type)}"


def find_media(url: str, media_dir: Path = MEDIA_DIR) -> Optional[str]:
    """File name stored for `url`, or None when it was never captured."""
    matches = sorted(media_dir.glob(f"{url_key(url)}.*")) if media_dir.is_dir() else []
    for match in matches:
        if MEDIA_NAME_RE.match(match.name) and match.stat().st_size > 0:
            return match.name
    return None


def screenshot_key(page_url: str) -> str:
    """Store key of the original page's screenshot (not a fetchable URL)."""
    return f"urltocode-screenshot:{page_url}"


def save_media(
    url: str,
    content_type: str,
    body: bytes,
    media_dir: Path = MEDIA_DIR,
    overwrite: bool = False,
) -> Optional[str]:
    """Store `body` for `url`; returns the file name, or None when refused.

    A URL's content rarely changes, so an existing file is kept; screenshots
    of a re-crawled page pass `overwrite` to show its current state.
    """
    kind = media_kind(content_type)
    if kind is None or not body:
        return None
    limit = MAX_VIDEO_BYTES if kind == "video" else MAX_IMAGE_BYTES
    if len(body) > limit:
        return None
    media_dir.mkdir(parents=True, exist_ok=True)
    name = media_filename(url, content_type)
    target = media_dir / name
    if overwrite or not target.exists():
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(target)
    return name


def rewrite_media_urls(code: str, media: Dict[str, str], base_url: str) -> str:
    """Point every captured media URL in `code` at its stored copy.

    `base_url` is where the backend serves MEDIA_ROUTE. Longer URLs go first
    so a URL that prefixes another is not rewritten inside it; the
    HTML-escaped spelling (&amp;) models write in attributes is covered too.
    """
    for url in sorted(media, key=len, reverse=True):
        local = f"{base_url}{MEDIA_ROUTE}/{media[url]}"
        code = code.replace(url, local)
        escaped = url.replace("&", "&amp;")
        if escaped != url:
            code = code.replace(escaped, local)
    return code


def media_path(name: str, media_dir: Path = MEDIA_DIR) -> Optional[Path]:
    """Path of a stored file, refusing anything but a store file name."""
    if not MEDIA_NAME_RE.match(name):
        return None
    path = media_dir / name
    return path if path.is_file() else None
