"""Finding the pages of a site that only exists in the browser.

Most sites are a list of documents, and following `<a href>` finds them all.
Three common shapes hide their pages from that:

* **Hash routes** - `#/products/42` never reaches the server, so the link looks
  like a same-document anchor and the route is invisible to a crawler.
* **pushState routes** - a client router changes the address bar with
  `history.pushState` and fetches the next view over XHR, so no document is
  ever requested for the new route.
* **XHR-delivered documents** - a framework asks the server for HTML (or a
  route manifest) after load, and the page the user sees was assembled in the
  browser.

The crawler stays observational - it never clicks - so everything here is
derived from what the loaded page already exposes: its anchors, what its
routing code did while it ran, and what the network returned. Routes discovered
this way are then loaded as ordinary pages.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Optional, Set, cast
from urllib.parse import urljoin, urlparse, urlunparse

# A hash that is a route, not an in-page anchor. `/pricing` and `#/pricing` are
# pages; `#pricing`, `#section-two` and `#` are jumps inside one.
_HASH_ROUTE_RE = re.compile(r"^#/(?P<route>[A-Za-z0-9][^\s]*)")

# A route that a site can never serve: an auth callback to a third party, a
# logout, a websocket endpoint.
_NOT_A_ROUTE = re.compile(
    r"^/(logout|signout|sign_out|auth/callback|oauth2?/callback|ws)(/|$)", re.I
)

# Path prefixes worth following when a request returns something HTML-ish.
# A manifest is the one document that reliably lists an SPA's own routes.
_ROUTE_MANIFEST = re.compile(r"(sitemap|routes?|manifest)\.(json|xml|html?)$", re.I)

# Keys a manifest is known to list its pages under. Every other key is
# descended into rather than read, because the shape of a manifest is the
# app's own choice.
_ROUTE_KEYS = frozenset({"loc", "url", "path", "href", "pathname", "route"})

MAX_DISCOVERED_ROUTES = 60


def is_hash_route(href: str) -> bool:
    """True when an anchor points at a hash route rather than a spot on the page."""
    return bool(_HASH_ROUTE_RE.match(href.strip()))


def normalise_hash_route(href: str) -> Optional[str]:
    """`#/products/42` as a URL path, or None when the anchor is not a route."""
    match = _HASH_ROUTE_RE.match(href.strip())
    if not match:
        return None
    route = match.group("route").split("?", 1)[0].split("#", 1)[0]
    route = route.rstrip("/")
    if not route:
        return None
    return "/" + route


def is_route_manifest(url: str) -> bool:
    """True for a sitemap or route manifest, the one place an SPA lists routes."""
    return bool(_ROUTE_MANIFEST.search(urlparse(url).path))


def same_origin(url: str, base: str) -> bool:
    """True for a URL on the crawled site, subdomains included."""
    host = urlparse(url).netloc
    base_host = urlparse(base).netloc
    if not host or not base_host:
        return False
    return host == base_host or host.endswith("." + base_host)


def clean(url: str) -> str:
    """The crawl identity of a URL: no fragment, no empty query, no trailing /."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", parsed.query, ""))


def route_from_xhr(request_url: str, base_url: str, content_type: str = "") -> Optional[str]:
    """A page URL implied by a background request, or None.

    Only two kinds of request are treated as a route: one that came back as a
    document (a framework fetching a partial page), and a sitemap or route
    manifest, which is the one place an SPA lists its own routes. An API call
    returning JSON is data, not a page, and turning `/api/posts` into a page
    would send the crawler after endpoints that render nothing on their own.
    """
    if not same_origin(request_url, base_url):
        return None

    parsed = urlparse(request_url)
    path = parsed.path.rstrip("/") or "/"
    if _NOT_A_ROUTE.match(path):
        return None

    is_document = any(
        kind in content_type.lower()
        for kind in ("text/html", "application/xhtml", "text/xml")
    )
    if is_document:
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    if _ROUTE_MANIFEST.search(path):
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return None


def parse_sitemap(text: str) -> List[str]:
    """Page URLs out of a sitemap document.

    XML sitemaps and JSON sitemaps are both just lists of URLs written in
    different shapes, so both are walked by `routes_from_manifest`; the XML is
    only unwrapped first. A document that is neither yields nothing.
    """
    stripped = text.lstrip()
    if stripped.startswith("<"):
        try:
            from xml.etree import ElementTree
        except ImportError:  # pragma: no cover - ElementTree is stdlib
            return []
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return []
        # Every <loc> is a page URL, wherever in the document it sits.
        return [
            (element.text or "").strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "loc" and (element.text or "").strip()
        ]
    try:
        return routes_from_manifest(json.loads(text), "")
    except (TypeError, ValueError):
        return []


def routes_from_manifest(manifest: object, base_url: str, limit: int = MAX_DISCOVERED_ROUTES) -> List[str]:
    """Page URLs listed by a sitemap or route manifest.

    Anything unrecognised is ignored rather than guessed at: a crawl of
    invented URLs wastes the page budget and tells the user nothing.
    """
    found: List[str] = []
    seen: Set[str] = set()

    def add(url: str) -> None:
        if not isinstance(url, str) or not url.strip():
            return
        absolute = urljoin(base_url, url.strip())
        if not same_origin(absolute, base_url):
            return
        identity = clean(absolute)
        if identity in seen:
            return
        seen.add(identity)
        found.append(absolute)

    def walk(node: object, depth: int = 0) -> None:
        if len(found) >= limit or depth > 6:
            return
        if isinstance(node, str):
            add(node)
        elif isinstance(node, list):
            for item in cast(List[object], node):
                walk(item, depth + 1)
        elif isinstance(node, dict):
            for key, value in cast(Dict[str, object], node).items():
                # A known key holding a string is a page URL; anything else is
                # a container to look inside, so a manifest that wraps its list
                # in `{"routes": [...]}` is read just as a flat one is.
                if key in _ROUTE_KEYS and isinstance(value, str):
                    add(value)
                else:
                    walk(value, depth + 1)

    walk(manifest)
    return found[:limit]


def hash_routes_in_links(links: Iterable[str], base_url: str) -> List[str]:
    """Hash routes among a page's anchors, as absolute URLs."""
    routes: List[str] = []
    for href in links:
        route = normalise_hash_route(href)
        if not route:
            continue
        absolute = urljoin(base_url, route)
        if same_origin(absolute, base_url):
            routes.append(absolute)
    return routes


def merge_discovered(
    known: Set[str],
    candidates: Iterable[str],
    base_url: str,
    limit: int = MAX_DISCOVERED_ROUTES,
) -> List[str]:
    """New page URLs to queue, without duplicates of what is already queued."""
    added: List[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        absolute = urljoin(base_url, candidate.strip())
        if not same_origin(absolute, base_url):
            continue
        identity = clean(absolute)
        if identity in known:
            continue
        known.add(identity)
        added.append(absolute)
        if len(added) >= limit:
            break
    return added


def record_push_state(entries: Iterable[str]) -> List[str]:
    """URLs a client router pushed while the page was loading.

    An SPA that navigates on its own has already told us which routes exist; a
    route it never visits is not one a visitor would see either.
    """
    return [entry for entry in entries if isinstance(entry, str) and entry.strip()]
