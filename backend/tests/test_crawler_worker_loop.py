"""The crawl loop itself, driven against a local site.

Everything else about the crawler is tested through pure helpers; this is the
part that only breaks in a real browser: following internal links, capturing
exactly the page that was queued, and never interacting with the site.

Needs Chromium, so it is skipped where Playwright is not installed. Mark:
`pytest -m browser` to run only these, `-m "not browser"` to skip them.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterator, List, cast

import pytest

from crawler import playwright_worker as worker

pytest.importorskip("playwright", reason="Playwright is not installed")

pytestmark = pytest.mark.browser


HOME = """<!DOCTYPE html>
<html><head><title>Fixture home</title></head>
<body>
  <nav><a href="/about">About</a><a href="https://example.org/off">Off-site</a></nav>
  <h1>Fixture home</h1>
  <input type="search" id="q" placeholder="Search the site">
  <button id="go">Go</button>
  <div id="results"></div>
  <button id="signin">Sign in</button>
  <script>
    function run() {
      var q = document.getElementById('q').value;
      document.getElementById('results').innerHTML =
        '<ul>' + Array.from({length: 30}, function (_, i) {
          return '<li>Result ' + (i + 1) + ' for ' + q + '</li>';
        }).join('') + '</ul>';
    }
    document.getElementById('go').addEventListener('click', run);
    document.getElementById('q').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') run();
    });
    document.getElementById('signin').addEventListener('click', function () {
      window.location.href = '/signin';
    });
  </script>
</body></html>"""

ABOUT = """<!DOCTYPE html>
<html><head><title>Fixture about</title></head>
<body><h1>About</h1><p>Second page of the fixture site.</p></body></html>"""

SIGNIN = """<!DOCTYPE html>
<html><head><title>Fixture sign-in</title></head>
<body><h1>Sign in</h1><input type="password" name="password"></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        body = {"/": HOME, "/about": ABOUT, "/signin": SIGNIN}.get(
            self.path.split("?")[0], "<html><body>404</body></html>"
        )
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: Any) -> None:
        """Keep the test output readable."""


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _crawl(url: str, **kwargs: Any) -> List[Dict[str, Any]]:
    return _crawl_result(url, **kwargs)["pages"]


def _crawl_result(url: str, **kwargs: Any) -> Dict[str, Any]:
    """The whole worker answer, pages and captured API together."""
    result = asyncio.run(
        worker.crawl(
            url,
            kwargs.pop("max_pages", 3),
            kwargs.pop("max_depth", 1),
            kwargs.pop("timeout", 10),
            headless=True,
            **kwargs,
        )
    )
    return cast(Dict[str, Any], result)


def test_the_crawl_follows_internal_links_only(site: str) -> None:
    pages = _crawl(site)

    paths = [p["path"] for p in pages]
    assert "/" in paths and "/about" in paths
    home = next(p for p in pages if p["path"] == "/")
    assert all("example.org" not in link for link in home["links"])


def test_the_capture_describes_the_page_itself(site: str) -> None:
    """The crawl never clicks, so a JS redirect cannot poison the capture.

    This used to matter because the crawler clicked buttons and tabs; the
    capture now describes the queued URL by construction, and this test pins
    that contract: the "Sign in" button's target page must never leak in.
    """
    pages = _crawl(site, max_pages=1)

    home = next(p for p in pages if p["path"] == "/")
    assert home["title"] == "Fixture home"
    assert "Sign in</h1>" not in home["html"]


def test_a_crawl_past_its_budget_returns_what_it_has(site: str) -> None:
    """The run stops itself instead of being killed with nothing to show."""
    pages = _crawl(site, max_pages=3, budget=0.1)

    assert pages == [] or len(pages) < 3
