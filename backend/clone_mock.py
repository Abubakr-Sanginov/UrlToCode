"""The API a site answers with, kept.

A cloned page is a list of things that loads nothing: the original filled it
by calling its own API, and nothing in the markup says what came back. The
screenshot shows a table with six rows; the clone has a table with a header.

So the crawl records what the site actually answered with. Every JSON response
it saw becomes an entry in a mock table that ships with the project, and a
small interceptor lets the pages read from it. The clone then shows the same
six rows, offline, with no backend behind it - which is the whole difference
between a picture of a site and something you can click through.

Only JSON, only small enough to be worth keeping, and never anything that
looks like a credential.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlsplit

# Big enough for a real payload, small enough that a runaway endpoint cannot
# fill the disk or the prompt.
MAX_BODY_BYTES = 400_000
MAX_ENTRIES_PER_PAGE = 25
MAX_ENTRIES_TOTAL = 120
MAX_DEPTH = 12

# What to record: the path and method, not the host. A captured response from
# a third-party CDN is not the site's own API, and its host would leak into
# every generated file.
_API_PATH = re.compile(r"^(/api/|/v\d+/|/rest/|/graphql|\.json$|/data/)", re.I)

# A key whose name says it holds a credential. The value is useless as mock
# data and a liability in a folder the user saves and shares.
_SECRET_KEY = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|\btoken\b|secret|password|"
    r"passwd|authorization|cookie|session[_-]?id|private[_-]?key|client[_-]?secret)",
    re.I,
)
_REDACTED = "[removed]"


@dataclass
class CapturedResponse:
    """One API answer, as the clone will replay it."""

    method: str
    path: str
    status: int
    body: Any

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"

    def to_json(self) -> Dict[str, Any]:
        return {"status": self.status, "body": self.body}


@dataclass
class Capture:
    """Everything the crawl saw, bounded and deduplicated."""

    entries: Dict[str, CapturedResponse] = field(default_factory=lambda: {})
    # The site being cloned. Responses from anywhere else are somebody else's
    # API and must not end up in the project.
    base_url: str = ""

    def add(
        self,
        method: str,
        url: str,
        status: int,
        content_type: str,
        raw: str,
    ) -> Optional[CapturedResponse]:
        """Record one response, or refuse it and say why by returning None."""
        if len(self.entries) >= MAX_ENTRIES_TOTAL:
            return None
        path = _api_path(url, self.base_url)
        if path is None:
            return None
        if not _is_json(content_type):
            return None
        if status >= 400:
            # An error body is the site's failure, not its data. Replaying it
            # would put an error message where the clone expects content.
            return None
        if len(raw) > MAX_BODY_BYTES:
            # Cutting a JSON document short leaves something that does not
            # parse, so an oversized answer is dropped rather than kept as
            # broken: the page still renders without it.
            return None
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            return None
        body = _scrub(body, 0)
        if body is None:
            return None

        entry = CapturedResponse(
            method=method.upper(), path=path, status=status, body=body
        )
        # First answer wins: a page polls its API, and the tenth poll of the
        # same endpoint is not a different answer.
        if entry.key not in self.entries:
            self.entries[entry.key] = entry
        return self.entries[entry.key]

    def add_page(self, page_capture: Capture) -> None:
        """Merge what one page saw into the run's capture."""
        for key, entry in page_capture.entries.items():
            if key in self.entries or len(self.entries) >= MAX_ENTRIES_TOTAL:
                continue
            self.entries[key] = entry

    @property
    def anything(self) -> bool:
        return bool(self.entries)

    def to_json(self) -> Dict[str, Any]:
        return {key: entry.to_json() for key, entry in self.entries.items()}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Capture":
        """Rebuild a capture from the crawl's stored answers.

        The crawl saves only what a later run can use, so the method and path
        are recovered from the key rather than stored twice; an entry that
        does not parse is dropped rather than half-read.
        """
        capture = cls()
        if not isinstance(data, dict):
            return capture
        entries: Dict[str, Any] = cast(Dict[str, Any], data)
        for raw_key, raw_value in entries.items():
            if not isinstance(raw_value, dict):
                continue
            value: Dict[str, Any] = cast(Dict[str, Any], raw_value)
            method, _, path = str(raw_key).partition(" ")
            if not path:
                method, path = "GET", str(raw_key)
            status = value.get("status")
            capture.entries[str(raw_key)] = CapturedResponse(
                method=method.upper(),
                path=path,
                status=int(status) if isinstance(status, (int, float)) else 200,
                body=value.get("body"),
            )
        return capture

    def summary(self) -> str:
        """A short list of what was captured, for the page prompt."""
        if not self.entries:
            return ""
        lines = "\n".join(f"- {key}" for key in sorted(self.entries)[:MAX_ENTRIES_TOTAL])
        return f"API RESPONSES CAPTURED (replayed from the original site):\n{lines}"


def _api_path(url: str, base_url: str = "") -> Optional[str]:
    """The path of a request to the site's own API, or None.

    The host matters as much as the path: an analytics beacon, a CDN and a
    payment widget all answer with JSON, and none of them are this site's
    data. Without the host, a clone's mock layer fills up with somebody
    else's endpoints.
    """
    parts = urlsplit(url)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if not _same_site(parts.netloc, base_url):
        return None
    path = parts.path or "/"
    if not _API_PATH.match(path):
        return None
    # A query string is per-visit state (a cursor, a filter the user applied),
    # not the shape of the resource, so it is dropped rather than turned into
    # a hundred near-duplicate entries.
    return path


def _same_site(netloc: str, base_url: str) -> bool:
    """Whether a request went to the site being cloned.

    A subdomain is the same site - `api.example.com` and `example.com` are
    one deployment, and the API is very often on the subdomain. A different
    root domain is not, however similar it looks.
    """
    if not base_url:
        # Nothing to compare against. A relative request has no host of its
        # own and is certainly the page's; an absolute one is not known to
        # be ours, so it is left out rather than guessed at.
        return not netloc
    host = netloc.split("@")[-1].split(":")[0].lower()
    root = urlsplit(base_url).netloc.split("@")[-1].split(":")[0].lower()
    if not host or not root:
        return not host
    return host == root or host.endswith("." + root)


def _is_json(content_type: str) -> bool:
    return "json" in (content_type or "").lower()


def _scrub(value: Any, depth: int) -> Any:
    """Remove anything that looks like a credential, at any depth.

    A payload can be arbitrarily deep and arbitrarily shaped, so this walks
    dictionaries and lists rather than looking for known keys, and gives up
    past a depth no real API response goes to.
    """
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        source: Dict[str, Any] = cast(Dict[str, Any], value)
        for raw_key, raw_value in source.items():
            key = str(raw_key)
            if _SECRET_KEY.search(key):
                cleaned[key] = _REDACTED
                continue
            scrubbed = _scrub(raw_value, depth + 1)
            if scrubbed is not None:
                cleaned[key] = scrubbed
        return cleaned
    if isinstance(value, list):
        items = [cast(Any, _scrub(cast(Any, item), depth + 1)) for item in cast(List[Any], value)]
        return [item for item in items if item is not None]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


# --- shipping the capture as a project -------------------------------------


def build_mock_layer(capture: Capture) -> Dict[str, str]:
    """The files that let the clone read the captured answers.

    An empty map when nothing was captured: a mock layer with no entries would
    intercept real requests and answer them with nothing, which is worse than
    not having one.
    """
    if not capture.anything:
        return {}

    return {
        "mock/api.json": json.dumps(capture.to_json(), indent=2, ensure_ascii=False) + "\n",
        "mock/mock.js": MOCK_JS,
        "mock/README.md": _readme(capture),
    }


MOCK_JS = """/**
 * Answers API calls from the captured responses, so the pages work offline.
 *
 * Load it before anything else on the page:
 *
 *     <script src="/mock/mock.js"></script>
 *
 * Then call the original API exactly as the cloned page does. The request is
 * answered from api.json if it was captured, and passes through to the real
 * server if it was not - so this can be left in while the backend catches up,
 * one endpoint at a time.
 */
(() => {
  const MOCKS = "/mock/api.json";

  function load() {
    return fetch(MOCKS)
      .then((response) => (response.ok ? response.json() : {}))
      .catch(() => ({}));
  }

  function lookup(table, url, method) {
    const path = new URL(url, window.location.origin).pathname;
    return (
      table[`${(method || "GET").toUpperCase()} ${path}`] ??
      table[`GET ${path}`] ??
      table[`POST ${path}`] ??
      null
    );
  }

  function answer(entry) {
    return Promise.resolve(
      new Response(JSON.stringify(entry.body), {
        status: entry.status,
        headers: { "Content-Type": "application/json" },
      })
    );
  }

  load().then((table) => {
    const keys = Object.keys(table);
    if (keys.length === 0) return;

    // eslint-disable-next-line no-console
    console.info(`[mock] answering ${keys.length} captured endpoints`);

    const realFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      try {
        const url = typeof input === "string" ? input : input && input.url;
        const method = (init && init.method) || (input && input.method) || "GET";
        const entry = lookup(table, url, method);
        if (entry) return answer(entry);
      } catch {
        // A malformed request is not ours to answer.
      }
      return realFetch(input, init);
    };

    const realOpen = XMLHttpRequest.prototype.open;
    const realSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__mockEntry = lookup(table, url, method);
      return realOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      if (this.__mockEntry) {
        Object.defineProperty(this, "readyState", { value: 4, configurable: true });
        Object.defineProperty(this, "status", {
          value: this.__mockEntry.status,
          configurable: true,
        });
        Object.defineProperty(this, "responseText", {
          value: JSON.stringify(this.__mockEntry.body),
          configurable: true,
        });
        Object.defineProperty(this, "response", {
          value: JSON.stringify(this.__mockEntry.body),
          configurable: true,
        });
        if (typeof realSend === "function") realSend.call(this);
        if (typeof this.onreadystatechange === "function") this.onreadystatechange();
        return;
      }
      return realSend.apply(this, arguments);
    };
  });
})();
"""


def _readme(capture: Capture) -> str:
    lines = [
        "# Captured API responses",
        "",
        "The original site filled its pages by calling an API. Those answers were",
        "recorded while the site was crawled and are saved here, so the clone can",
        "show the same content without the original server behind it.",
        "",
        "## Use them",
        "",
        "Add this to the top of any page, before the scripts that load data:",
        "",
        "```html",
        '<script src="/mock/mock.js"></script>',
        "```",
        "",
        "The pages can then call the API exactly as before. A request that was",
        "captured is answered locally; anything else passes through to a real",
        "server, so the mock layer can be left in while the backend catches up.",
        "",
        "## What is in here",
        "",
    ]
    for key in sorted(capture.entries):
        entry = capture.entries[key]
        lines.append(f"- `{key}` → {entry.status} {_describe(entry.body)}")
    lines.append("")
    lines.append("Values under a key that looked like a credential were replaced")
    lines.append("with `[removed]` rather than kept.")
    lines.append("")
    return "\n".join(lines)


def _describe(body: Any) -> str:
    """One short phrase for a payload, so the list is readable."""
    if isinstance(body, list):
        return f"a list of {len(body)}"
    if isinstance(body, dict):
        keys = ", ".join(list(body)[:4])
        return f"an object with {keys}" if keys else "an empty object"
    if isinstance(body, str):
        return "a string"
    if isinstance(body, (int, float, bool)) or body is None:
        return "a single value"
    return "a value"