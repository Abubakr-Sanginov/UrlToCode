"""Listening to a page's own router while it loads.

A hash-routed or client-routed app never asks the network for its pages: the
router changes the address bar in JavaScript and fetches whatever the new view
needs. That is all still observable — the page is loaded, the router runs, and
the requests it makes are visible — so the routes can be recorded without ever
clicking a link.

The script is installed before the first navigation and removed after the
page's own load, so nothing here runs while the clone's own code does.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, TypedDict, cast

# Installed with `add_init_script`, so it runs before any page script and sees
# every pushState from the app's very first render.
ROUTE_RECORDER_JS = """
(() => {
  if (window.__utcRoutes) return;
  const record = { pushes: [], requests: [] };
  window.__utcRoutes = record;

  const push = history.pushState.bind(history);
  history.pushState = function (state, title, url) {
    try { if (url) record.pushes.push(String(url)); } catch (e) {}
    return push(state, title, url);
  };

  const replace = history.replaceState.bind(history);
  history.replaceState = function (state, title, url) {
    try { if (url) record.pushes.push(String(url)); } catch (e) {}
    return replace(state, title, url);
  };

  window.addEventListener('hashchange', () => {
    try { record.pushes.push(location.hash); } catch (e) {}
  });

  // A framework that fetches the next view over the network is telling us the
  // route exists; the response's type is decided on the Node side.
  const note = (url) => {
    try {
      if (url && typeof url === 'string' && url.indexOf(location.origin) === 0) {
        record.requests.push(url);
      }
    } catch (e) {}
  };

  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function (input, init) {
      try {
        note(typeof input === 'string' ? input : (input && input.url));
      } catch (e) {}
      return originalFetch.apply(this, arguments);
    };
  }

  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    try { note(String(url)); } catch (e) {}
    return open.apply(this, arguments);
  };
})();
"""


class RouteRecord(TypedDict):
    """What a page's own router did while it loaded."""

    pushes: List[str]
    requests: List[str]


async def read_route_recorder(page: Any) -> RouteRecord:
    """Whatever the recorder caught on this page, or an empty record.

    Async because Playwright's page is: calling `evaluate` without awaiting
    it leaves a coroutine nobody runs, the warning it prints on every crawl
    is the only sign, and the record comes back empty - so client-side
    routes were never recorded at all, silently, on every page.
    """
    empty: RouteRecord = {"pushes": [], "requests": []}
    try:
        raw = await page.evaluate("() => JSON.stringify(window.__utcRoutes || null)")
    except Exception:
        return empty
    if not raw:
        return empty
    try:
        parsed: object = json.loads(raw)
    except (TypeError, ValueError):
        return empty
    if not isinstance(parsed, dict):
        return empty
    record = cast(Dict[str, object], parsed)

    def strings(key: str) -> List[str]:
        values = record.get(key)
        if not isinstance(values, list):
            return []
        return [item for item in cast(List[object], values) if isinstance(item, str)]

    return {"pushes": strings("pushes"), "requests": strings("requests")}
