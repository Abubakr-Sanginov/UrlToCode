"""Not paying twice for the same page.

Cloning a site is the kind of thing people do twice: the first attempt hits a
rate limit, or the model returns something the visual check hates, or the tab
closes halfway through. Every one of those restarts the model from nothing,
and the parts that were already right are paid for again.

So a finished page is kept, keyed by everything that decides what the model
would say: the site it came from, the page on that site, the stack, the
model, and a version of the prompt itself. Change any of them and the old
answer is not reused - a page built by yesterday's prompt is not the page
this prompt would have produced, and quietly handing it back is how a tool
starts returning stale output and calling it a cache.

The cache is deliberately dull: a directory of JSON files, bounded by count
and age, readable and deleteable by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import clone_runs

# A clone of the same site on the same day is the case worth covering. Past
# that, a site has usually changed enough that the old answer is a worse
# starting point than a fresh one.
MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# Roughly a hundred clones' worth of pages. Each entry is a page of HTML,
# which is a few kilobytes, so this is a few megabytes.
MAX_ENTRIES = 400

CACHE_DIR = clone_runs.CLONE_RUNS_DIR / "page-cache"


@dataclass
class CacheKey:
    """Everything that decides what the model would answer."""

    base_url: str
    path: str
    stack: str
    model: str
    prompt_version: str
    generate_database: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "baseUrl": self.base_url,
            "path": self.path,
            "stack": self.stack,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "generateDatabase": self.generate_database,
        }

    def token(self) -> str:
        """A short stable name for this combination of settings."""
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def file(self) -> Path:
        return CACHE_DIR / f"{self.token()}.json"


@dataclass
class CacheEntry:
    code: str
    stored_at: float
    key: Dict[str, Any]

    def to_json(self) -> Dict[str, Any]:
        return {"code": self.code, "storedAt": self.stored_at, "key": self.key}


def key_for(
    base_url: str,
    path: str,
    stack: str,
    model: str,
    prompt_version: str,
    generate_database: bool = False,
) -> CacheKey:
    return CacheKey(
        base_url=base_url,
        path=path,
        stack=stack,
        model=model,
        prompt_version=prompt_version,
        generate_database=generate_database,
    )


def get(key: CacheKey) -> Optional[str]:
    """A previously generated page, or None.

    A cache that cannot be read is a cache miss, never an error: the caller
    has a perfectly good fallback, which is to ask the model.
    """
    try:
        raw = json.loads(key.file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    entry: Dict[str, Any] = cast(Dict[str, Any], raw)
    code = entry.get("code")
    stored_at = entry.get("storedAt")
    if not isinstance(code, str) or not code.strip():
        return None
    if not isinstance(stored_at, (int, float)):
        return None
    if time.time() - float(stored_at) > MAX_AGE_SECONDS:
        # Expired: the site has probably changed, and a stale page is a
        # worse answer than a new one even though it was free.
        return None
    return code


def put(key: CacheKey, code: str) -> None:
    """Keep a generated page, if it is worth keeping."""
    if not code or not code.strip():
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = key.file()
        tmp = target.with_suffix(".json.tmp")
        # Written to one side and moved into place, so a crash halfway
        # through cannot leave a truncated page that reads as a hit.
        tmp.write_text(
            json.dumps(CacheEntry(code=code, stored_at=time.time(), key=key.as_dict()).to_json()),
            encoding="utf-8",
        )
        os.replace(tmp, target)
    except OSError as exc:
        print(f"[Cache] Could not store {key.path}: {exc}")
        return
    _prune()


def _prune() -> None:
    """Drop what is over the limit, oldest first."""
    try:
        paths = list(CACHE_DIR.glob("*.json"))
    except OSError:
        return

    now = time.time()
    stored: List[tuple[float, Path]] = []
    for path in paths:
        when = 0.0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("storedAt"), (int, float)):
                when = float(raw["storedAt"])
        except (OSError, ValueError):
            # A file that cannot be read is as good as expired, and counts as
            # the oldest thing here so it is the first to go.
            when = 0.0
        stored.append((when, path))

    fresh = sorted(stored, key=lambda item: item[0])
    # Oldest first, so the newest MAX_ENTRIES are the ones kept: a page
    # generated seconds ago is worth more than one from earlier in the same
    # session, and dropping the wrong end of the list empties the cache of
    # exactly the answers someone is about to ask for again.
    survivors = fresh[-MAX_ENTRIES:] if MAX_ENTRIES > 0 else []
    keep = {path for _, path in survivors}
    now = time.time()
    for when, path in stored:
        if path not in keep or now - when > MAX_AGE_SECONDS:
            _remove(path)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def stats() -> Dict[str, Any]:
    """What the cache is holding, for the settings panel."""
    total = 0
    oldest = 0.0
    try:
        paths = list(CACHE_DIR.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            total += path.stat().st_size
            when = float(json.loads(path.read_text(encoding="utf-8")).get("storedAt") or 0)
            oldest = min(oldest, when) if oldest else when
        except (OSError, ValueError, TypeError):
            continue
    return {
        "entries": len(paths),
        "bytes": total,
        "oldest": oldest,
        "directory": str(CACHE_DIR),
    }


def clear() -> int:
    """Empty the cache, answering how many entries went."""
    removed = 0
    try:
        paths = list(CACHE_DIR.glob("*.json"))
    except OSError:
        return 0
    for path in paths:
        _remove(path)
        removed += 1
    return removed
