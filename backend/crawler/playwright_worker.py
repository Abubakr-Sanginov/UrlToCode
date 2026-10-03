import asyncio
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urljoin, urlparse, urlunparse

# Run as a script (`python crawler/playwright_worker.py`), so the backend
# package root is not on the path by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CRAWLER_HEADLESS  # noqa: E402  (path set up above)
from llm_http import read_worker_params  # noqa: E402  (path set up above)
from crawler.media_store import (  # noqa: E402  (path set up above)
    MAX_CRAWL_MEDIA_BYTES,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    find_media,
    media_kind,
    save_media,
    screenshot_key,
)
from crawler import route_discovery  # noqa: E402  (path set up above)
from crawler import route_recorder  # noqa: E402  (path set up above)
from crawler import responsive  # noqa: E402  (path set up above)
from crawler import interaction_states  # noqa: E402  (path set up above)
from crawler import embedded as embedded_content  # noqa: E402  (path set up above)
import clone_mock  # noqa: E402  (path set up above)

# A sitemap is fetched with the request context, not the page, so a slow or
# huge one cannot stall the crawl.
MANIFEST_TIMEOUT_MS = 8000
MAX_MANIFEST_BYTES = 1_000_000


def normalize(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", p.query, ""))


def same_domain(url: str, base: str) -> bool:
    ud: str = urlparse(url).netloc
    bd: str = urlparse(base).netloc
    return ud == bd or ud.endswith("." + bd)


async def scroll_page(page):
    """Scroll down the page to trigger lazy-loaded content.

    Scrolling is observation, not interaction: it never changes page state,
    it only lets the page finish rendering what a visitor would see.
    """
    try:
        for i in range(5):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(400)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


# Bot-check interstitials ("Just a moment…", "Checking your browser"). Cloning
# one of these produces a blank page, so they are waited out and, if they
# persist, reported instead of being handed to the model as if they were the
# site.
CHALLENGE_TITLE_RE = re.compile(
    "just a moment|checking your browser|attention required|"
    "verifying you are human|Один момент|"
    "проверка браузера",
    re.IGNORECASE,
)


async def _is_challenge_page(page: Any) -> bool:
    try:
        title: str = str(await page.title() or "")
    except Exception:
        return False
    if CHALLENGE_TITLE_RE.search(title):
        return True
    try:
        html: str = str(await page.content() or "")
    except Exception:
        return False
    return "cf-challenge" in html or "challenge-platform" in html


async def wait_out_bot_check(page: Any, max_wait: float = 20.0) -> bool:
    """Give a Cloudflare-style check time to clear itself.

    Returns True if the page is still a challenge afterwards.
    """
    if not await _is_challenge_page(page):
        return False

    print("[Worker] Bot check detected, waiting it out", file=sys.stderr, flush=True)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        await page.wait_for_timeout(2000)
        if not await _is_challenge_page(page):
            print("[Worker] Bot check cleared", file=sys.stderr, flush=True)
            return False
    print("[Worker] Bot check did not clear", file=sys.stderr, flush=True)
    return True


# Resolves what the page actually shows into plain attributes before the
# markup is captured: the image variant the browser picked (srcset, lazy
# data-src), CSS background images (as data-bg-image, which the compactor
# keeps) and <video> sources and posters. Returns the media, in page order.
_PREPARE_MEDIA_JS = r"""() => {
  const abs = (u) => { try { return new URL(u, document.baseURI).href; } catch (e) { return ""; } };
  const usable = (u) => u && !u.startsWith("data:") && !u.startsWith("blob:");
  const images = [];
  const videos = [];
  const addImage = (u) => { if (usable(u) && !images.includes(u)) images.push(u); };

  for (const img of document.querySelectorAll("img")) {
    let src = img.currentSrc || img.src || "";
    const lazy = img.getAttribute("data-src") || img.getAttribute("data-lazy-src") || img.getAttribute("data-original");
    if (!usable(src) && lazy) src = abs(lazy);
    if (usable(src)) {
      img.setAttribute("src", src);
      // Tracking pixels are not content.
      const pixel = img.complete && img.naturalWidth <= 2 && img.naturalHeight <= 2;
      if (!pixel) addImage(src);
    }
  }

  const elements = document.querySelectorAll("body *");
  for (let i = 0; i < elements.length && i < 5000; i++) {
    const el = elements[i];
    const bg = getComputedStyle(el).backgroundImage;
    if (!bg || bg === "none") continue;
    const match = bg.match(/url\(["']?([^"')]+)["']?\)/);
    if (!match) continue;
    const u = abs(match[1]);
    if (!usable(u)) continue;
    el.setAttribute("data-bg-image", u);
    addImage(u);
  }

  for (const video of document.querySelectorAll("video")) {
    let src = video.currentSrc || video.getAttribute("src") || "";
    const source = video.querySelector("source[src]");
    if (!usable(src) && source) src = source.src;
    src = usable(src) ? abs(src) : "";
    const poster = video.getAttribute("poster") ? abs(video.getAttribute("poster")) : "";
    if (src) video.setAttribute("src", src);
    if (poster) {
      video.setAttribute("poster", poster);
      addImage(poster);
    }
    if ((src || poster) && !videos.some((v) => v.src === src && v.poster === poster)) {
      videos.push({ src: src, poster: poster });
    }
  }
  return { images: images, videos: videos };
}"""

# Media downloads per page beyond what the page loaded by itself.
MAX_FETCHED_IMAGES_PER_PAGE = 40
MAX_FETCHED_VIDEOS_PER_PAGE = 4


class MediaCapture:
    """Saves the images and videos the crawled pages load.

    Responses are stored straight from the network as the browser receives
    them, which covers images behind signed URLs or hotlink protection. What
    the page references but never fully loaded (videos stream in partial
    ranges, lazy images below the fold) is fetched afterwards with the
    browser's cookies.
    """

    def __init__(self) -> None:
        self.saved: Dict[str, str] = {}
        self.failed: set[str] = set()
        self.total_bytes = 0
        self._pending: List["asyncio.Future[None]"] = []

    def has_room(self, size: int) -> bool:
        return self.total_bytes + size <= MAX_CRAWL_MEDIA_BYTES

    def _store(self, url: str, content_type: str, body: bytes) -> None:
        if not self.has_room(len(body)):
            return
        name = save_media(url, content_type, body)
        if name:
            self.saved[url] = name
            self.total_bytes += len(body)

    def on_response(self, response: Any) -> None:
        self._pending.append(asyncio.ensure_future(self._from_response(response)))

    async def _from_response(self, response: Any) -> None:
        try:
            url: str = response.url
            if url in self.saved or not url.startswith("http"):
                return
            if response.request.resource_type not in ("image", "media"):
                return
            # A 206 is one range of a streamed video; it is fetched whole later.
            if response.status != 200:
                return
            content_type = response.headers.get("content-type", "")
            if media_kind(content_type) is None:
                return
            length = int(response.headers.get("content-length") or 0)
            if length > MAX_VIDEO_BYTES:
                return
            self._store(url, content_type, await response.body())
        except Exception:
            # Bodies of responses from a page that navigated away are gone.
            pass

    async def settle(self, timeout: float = 15.0) -> None:
        """Wait for in-flight response bodies to be written."""
        pending, self._pending = self._pending, []
        if not pending:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
        except asyncio.TimeoutError:
            pass

    async def fetch(self, context: Any, url: str, referer: str, kind: str) -> None:
        """Download a referenced file the page did not load in full."""
        if url in self.saved or url in self.failed or not url.startswith("http"):
            return
        known = find_media(url)
        if known:
            self.saved[url] = known
            return
        limit = MAX_VIDEO_BYTES if kind == "video" else MAX_IMAGE_BYTES
        try:
            head = await context.request.head(
                url, headers={"referer": referer}, timeout=10000, fail_on_status_code=False
            )
            if int(head.headers.get("content-length") or 0) > limit:
                self.failed.add(url)
                return
            response = await context.request.get(
                url,
                headers={"referer": referer},
                timeout=60000 if kind == "video" else 15000,
                fail_on_status_code=False,
            )
            if response.status != 200:
                self.failed.add(url)
                return
            self._store(url, response.headers.get("content-type", ""), await response.body())
        except Exception as e:
            self.failed.add(url)
            print(f"[Worker]   media fetch failed {url[:80]}: {e}", file=sys.stderr, flush=True)

    def files_for(self, urls: List[str]) -> Dict[str, str]:
        return {url: self.saved[url] for url in urls if url in self.saved}


# What the page looks like beyond its markup: the fonts and colors it
# actually renders with (computed styles, not guesses from the source), the
# web font stylesheets it loads, and the head metadata a clone should keep.
_DESIGN_JS = r"""() => {
  const hex = (c) => {
    const m = c && c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    if (!m || (m[4] !== undefined && parseFloat(m[4]) < 0.5)) return "";
    return "#" + [m[1], m[2], m[3]].map((n) => (+n).toString(16).padStart(2, "0")).join("");
  };
  const family = (s) => s.fontFamily.split(",")[0].replace(/["']/g, "").trim();
  const tally = (map, key, weight) => {
    if (key && weight > 0) map[key] = (map[key] || 0) + weight;
  };
  const ranked = (map) => Object.entries(map).sort((a, b) => b[1] - a[1]).map((e) => e[0]);

  // Only what a visitor sees counts, weighted by how much of it they see:
  // text colors by the amount of text, backgrounds by the area they cover.
  // Picking "the first h1" or "the first link" measured hidden menus and
  // one-off buttons, and the clone was recolored after them.
  const text = {}, background = {}, headings = {}, links = {};
  const buttonBg = {}, buttonText = {}, bodyFonts = {}, headingFonts = {};
  const bodySizes = {}, headingSizes = {};
  const viewportArea = window.innerWidth * window.innerHeight;

  for (const el of [document.documentElement, document.body]) {
    const s = getComputedStyle(el);
    tally(background, hex(s.backgroundColor), viewportArea / 1000);
  }

  const elements = document.querySelectorAll("body *");
  for (let i = 0; i < elements.length && i < 4000; i++) {
    const el = elements[i];
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || parseFloat(s.opacity || "1") < 0.1) continue;

    let own = 0;
    for (const node of el.childNodes) {
      if (node.nodeType === 3) own += node.textContent.trim().length;
    }
    const bg = hex(s.backgroundColor);
    tally(background, bg, Math.min(rect.width * rect.height, viewportArea) / 1000);

    const tag = el.tagName;
    const button =
      tag === "BUTTON" || el.getAttribute("role") === "button" || (tag === "A" && bg);
    if (button && bg) {
      tally(buttonBg, bg, 1);
      tally(buttonText, hex(s.color), 1);
    }
    if (!own) continue;

    tally(text, hex(s.color), own);
    if (el.closest("h1, h2, h3")) {
      tally(headings, hex(s.color), own);
      tally(headingFonts, family(s), own);
      tally(headingSizes, s.fontSize, own);
    } else {
      tally(bodyFonts, family(s), own);
      tally(bodySizes, s.fontSize, own);
      if (tag === "A" && !bg && el.closest("p, li")) tally(links, hex(s.color), own);
    }
  }

  const pageBackground = ranked(background)[0] || "#ffffff";
  const primary = ranked(buttonBg).filter((c) => c !== pageBackground);
  const palette = [];
  for (const c of [...ranked(text).slice(0, 4), ...ranked(background).slice(0, 4), ...primary.slice(0, 2)]) {
    if (!palette.includes(c)) palette.push(c);
  }

  const fontLinks = Array.from(document.querySelectorAll("link[rel=stylesheet][href]"))
    .map((l) => l.href)
    .filter((h) => /fonts\.googleapis\.com|fonts\.bunny\.net|use\.typekit\.net|fonts\.cdnfonts\.com/.test(h))
    .slice(0, 4);

  const meta = (sel) => {
    const m = document.querySelector(sel);
    return m ? (m.getAttribute("content") || "").trim() : "";
  };
  const icon = document.querySelector(
    "link[rel~='icon'][href], link[rel='shortcut icon'][href], link[rel='apple-touch-icon'][href]"
  );
  return {
    design: {
      bodyFont: ranked(bodyFonts)[0] || family(getComputedStyle(document.body)),
      headingFont: ranked(headingFonts)[0] || "",
      textColor: ranked(text)[0] || "",
      backgroundColor: pageBackground,
      headingColor: ranked(headings)[0] || "",
      linkColor: ranked(links)[0] || "",
      buttonColor: primary[0] || "",
      buttonTextColor: primary[0] ? ranked(buttonText)[0] || "" : "",
      baseFontSize: ranked(bodySizes)[0] || "",
      headingFontSize: ranked(headingSizes)[0] || "",
      palette: palette,
      fontLinks: fontLinks,
    },
    meta: {
      lang: document.documentElement.lang || "",
      description: meta("meta[name=description]"),
      ogImage: meta("meta[property='og:image']"),
      themeColor: meta("meta[name=theme-color]"),
      favicon: icon ? icon.href : new URL("/favicon.ico", location.origin).href,
    },
  };
}"""


async def collect_design(page: Any) -> Dict[str, Any]:
    """Computed fonts/colors and head metadata of the rendered page."""
    try:
        found: Dict[str, Any] = await page.evaluate(_DESIGN_JS)
        return found
    except Exception as e:
        print(f"[Worker]   design scan failed: {e}", file=sys.stderr, flush=True)
        return {"design": {}, "meta": {}}


# Full-page screenshots are cut here: a long page would otherwise be a
# 30 000 px image nobody scrolls through.
MAX_SCREENSHOT_HEIGHT = 6000


async def capture_screenshot(page: Any, url: str, viewport: Optional[responsive.Viewport] = None) -> str:
    """Store a full-page JPEG of the original; returns its store file name.

    With a viewport given, the page is re-laid out at that width first: a
    screenshot taken at 375px of a page still laid out at 1366px is the desktop
    page with its edges cut off, which tells nobody anything about mobile.
    """
    try:
        target = viewport or responsive.Viewport("desktop", responsive.CANONICAL_WIDTH, 768)
        if target.width != responsive.CANONICAL_WIDTH:
            await page.set_viewport_size({"width": target.width, "height": target.height})
            # The page has to re-lay out and finish reflowing before it is
            # worth photographing; a capture taken immediately is a frame of
            # the transition, not of the layout.
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            await page.wait_for_timeout(600)

        size = page.viewport_size or {"width": target.width, "height": target.height}
        height = int(await page.evaluate("document.documentElement.scrollHeight") or 0)
        height = max(min(height, MAX_SCREENSHOT_HEIGHT), int(size["height"]))
        shot: bytes = await page.screenshot(
            type="jpeg",
            quality=70,
            full_page=True,
            clip={"x": 0, "y": 0, "width": size["width"], "height": height},
        )
        stored = save_media(
            responsive.screenshot_file_for(screenshot_key(url), target.name),
            "image/jpeg",
            shot,
            overwrite=True,
        )
        # The page keeps the width it was captured at for the rest of its own
        # capture, so the width is restored before the caller goes on.
        if target.width != responsive.CANONICAL_WIDTH:
            await page.set_viewport_size(
                {"width": responsive.CANONICAL_WIDTH, "height": int(size["height"])}
            )
        return stored or ""
    except Exception as e:
        print(f"[Worker]   screenshot failed: {e}", file=sys.stderr, flush=True)
        return ""


async def collect_media(
    page: Any,
    context: Any,
    media: MediaCapture,
    fetch_missing: bool,
    extra_images: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolve the page's media in the DOM and make sure it is stored."""
    try:
        found: Dict[str, Any] = await page.evaluate(_PREPARE_MEDIA_JS)
    except Exception as e:
        print(f"[Worker]   media scan failed: {e}", file=sys.stderr, flush=True)
        found = {"images": [], "videos": []}

    images: List[str] = [u for u in found.get("images", []) if isinstance(u, str)]
    videos: List[Dict[str, str]] = [
        {"src": str(v.get("src") or ""), "poster": str(v.get("poster") or "")}
        for v in found.get("videos", [])
        if isinstance(v, dict)
    ]

    await media.settle()
    if fetch_missing:
        for url in images[:MAX_FETCHED_IMAGES_PER_PAGE]:
            await media.fetch(context, url, page.url, "image")
        for video in videos[:MAX_FETCHED_VIDEOS_PER_PAGE]:
            if video["src"]:
                await media.fetch(context, video["src"], page.url, "video")
    # Head images (favicon) are wanted even when the crawl is short on time.
    extras = [u for u in (extra_images or []) if u]
    for url in extras:
        await media.fetch(context, url, page.url, "image")

    urls = images + [v["src"] for v in videos if v["src"]] + extras
    return {"images": images, "videos": videos, "media": media.files_for(urls)}


async def capture_interaction_states(
    page: Any, url: str, base_url: str, limit: int
) -> List[Dict[str, Any]]:
    """Photograph the states a page reveals when its own controls are used.

    Opt-in, because this is the one part of the crawl that acts rather than
    watches. It clicks only controls the page itself marks as opening
    something, never submits a form, and puts the page back the way it found
    it after every one - so the capture that follows describes the same page
    the visitor would land on.
    """
    states: List[Dict[str, Any]] = []
    script = interaction_states.PROBE_JS % (
        json.dumps(interaction_states.CLICKABLE),
        json.dumps(bool(interaction_states.OPEN_PATTERN)),
        interaction_states.MAX_NODES,
    )
    try:
        found = await page.evaluate(script)
    except Exception as e:
        print(f"[Worker]   state probe failed: {e}", file=sys.stderr, flush=True)
        return states

    probed: Optional[Dict[str, Any]] = (
        cast(Dict[str, Any], found) if isinstance(found, dict) else None
    )
    nodes: Any = probed.get("nodes") if probed is not None else None
    if not isinstance(nodes, list):
        return states
    candidates = cast(List[Dict[str, Any]], nodes)

    for node in interaction_states.choose_controls(candidates, limit):
        selector = str(node.get("selector") or "")
        label = str(node.get("label") or "")
        index = 0
        # The probe listed every node for each selector in document order, so
        # the one chosen here is the nth that matches and is safe to click.
        try:
            index = await page.evaluate(
                "(s) => Array.from(document.querySelectorAll(s))"
                ".findIndex(n => (n.innerText || n.value || n.getAttribute('aria-label')"
                " || '').trim().slice(0, 80) === %s)" % json.dumps(label),
                selector,
            )
        except Exception:
            index = 0
        if index is None or index < 0:
            continue

        # Reloading between states is what makes the capture repeatable: two
        # menus opened one after another would show the second one over the
        # first, and a page left scrolled half way down is not what a visitor
        # sees.
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("body", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        try:
            clicked = await page.evaluate(
                interaction_states.click_script(selector, int(index))
            )
        except Exception as e:
            print(f"[Worker]   state click failed: {e}", file=sys.stderr, flush=True)
            continue
        if not clicked:
            continue
        await page.wait_for_timeout(700)

        # A click that navigated is not a state of this page; it is another
        # page, and the crawl will reach it on its own.
        try:
            if not same_domain(page.url, base_url):
                continue
        except Exception:
            continue

        shot = await capture_screenshot(page, f"{url}#{interaction_states.state_name_for(node)}")
        if not shot:
            continue
        states.append(
            {
                "name": interaction_states.state_name_for(node),
                "selector": selector,
                "label": label,
                "screenshot": shot,
                "url": page.url,
            }
        )
        if len(states) >= limit:
            break

    # The page is put back before the caller takes its own capture, so the
    # clone describes the state a visitor lands on and not the last menu.
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector("body", timeout=5000)
        await page.wait_for_timeout(800)
    except Exception as e:
        print(f"[Worker]   could not restore the page: {e}", file=sys.stderr, flush=True)

    return states


async def _capture_page_data(
    page: Any, url: str, base_url: str, depth: int, capture_screenshots: bool,
    media_data: Optional[Dict[str, Any]] = None,
    viewports: Optional[List[responsive.Viewport]] = None,
) -> Dict[str, Any]:
    """Capture all page data: title, html, links, forms, nav, images."""
    try:
        title = await page.title()
    except Exception:
        title = ""

    try:
        html = await page.content()
    except Exception:
        html = ""

    # `page.content()` is the host document only, so a page that is mostly a
    # map or a checkout widget arrives here as an empty div. The browser can
    # still read the frames and shadow roots it rendered.
    embedded = await embedded_content.collect_embedded(page)

    widths = viewports or responsive.responsive(False)
    # The desktop capture is the page's own screenshot, exactly as before, so
    # everything downstream keeps working unchanged.
    screenshot_file = ""
    viewport_screenshots: Dict[str, Dict[str, Any]] = {}
    if capture_screenshots:
        for index, viewport in enumerate(widths):
            shot = await capture_screenshot(page, url, viewport)
            if not shot:
                continue
            if index == 0:
                screenshot_file = shot
            height = 0
            try:
                height = int(
                    await page.evaluate("document.documentElement.scrollHeight") or 0
                )
            except Exception:
                height = 0
            viewport_screenshots[viewport.name] = {
                "file": shot,
                "width": viewport.width,
                "height": height,
            }

    links: List[str] = []
    hash_routes: List[str] = []
    try:
        anchors = await page.query_selector_all("a[href]")
        for el in anchors:
            try:
                href = await el.get_attribute("href")
                if not href or href.startswith("javascript") or href.startswith("mailto:"):
                    continue
                # `#/pricing` is a page in a hash-routed app; `#section` is a
                # jump inside this one. Only the first is a route to follow.
                if href.startswith("#"):
                    route = route_discovery.normalise_hash_route(href)
                    if route:
                        hash_routes.append(urljoin(page.url, route))
                    continue
                full = urljoin(page.url, href)
                if same_domain(full, base_url):
                    links.append(normalize(full))
            except Exception:
                continue
    except Exception as e:
        # Silently returning zero links made a broken capture look like a
        # single-page site, so the whole crawl stopped after one page.
        print(f"[Worker] Link extraction failed: {e}", file=sys.stderr, flush=True)

    forms: List[Dict[str, Any]] = []
    try:
        for f in await page.query_selector_all("form"):
            try:
                fd: Dict[str, Any] = {
                    "action": await f.get_attribute("action") or "",
                    "method": (await f.get_attribute("method") or "GET").upper(),
                    "inputs": [],
                }
                for inp in await f.query_selector_all("input, textarea, select"):
                    fd["inputs"].append({
                        "type": await inp.get_attribute("type") or "text",
                        "name": await inp.get_attribute("name") or "",
                        "placeholder": await inp.get_attribute("placeholder") or "",
                    })
                forms.append(fd)
            except Exception:
                continue
    except Exception:
        pass

    nav: List[Dict[str, str]] = []
    try:
        for el in await page.query_selector_all("nav a[href], header a[href], aside a[href]"):
            try:
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href")
                if text and href:
                    nav.append({"text": text[:50], "href": urljoin(url, href)})
            except Exception:
                continue
    except Exception:
        pass

    imgs: List[str] = list((media_data or {}).get("images", []))
    if not imgs:
        try:
            for img in await page.query_selector_all("img[src]"):
                try:
                    src = await img.get_attribute("src")
                    if src:
                        imgs.append(urljoin(url, src))
                except Exception:
                    continue
        except Exception:
            pass

    parsed_url = urlparse(url)
    ppath = parsed_url.path.rstrip("/") or "/"

    return {
        "url": url,
        "path": ppath,
        "title": title,
        "html": html,
        "embedded": embedded.to_json(),
        "screenshot": "",
        "screenshot_file": screenshot_file,
        "states": [],
        "viewport_screenshots": viewport_screenshots,
        "layout_notes": responsive.layout_notes(viewport_screenshots),
        "links": list(set(links)),
        "hash_routes": list(set(hash_routes)),
        "forms": forms,
        "navigation": nav,
        "images": imgs,
        "videos": (media_data or {}).get("videos", []),
        "media": (media_data or {}).get("media", {}),
        "design": (media_data or {}).get("design", {}),
        "meta": (media_data or {}).get("meta", {}),
        "depth": depth,
    }


async def _record_api(response: Any, capture: "clone_mock.Capture") -> None:
    """Keep an API answer so the clone can replay it.

    The body is read on the spot because a Playwright response body is only
    available before the page is closed. Anything unreadable, too large or not
    JSON is simply not kept: a page still renders without its data, and a mock
    layer full of error pages would be worse than none.
    """
    try:
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            return
        raw = await response.text()
        if not raw:
            return
        capture.add(
            method=response.request.method,
            url=response.url,
            status=response.status,
            content_type=content_type,
            raw=raw,
        )
    except Exception:
        # Redirects, aborted requests and closed pages all land here.
        pass


def _schedule_api_capture(response: Any, capture: "clone_mock.Capture") -> None:
    """Start reading one response body, from a callback that cannot wait."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    task = loop.create_task(_record_api(response, capture))
    _api_tasks.add(task)
    task.add_done_callback(_api_tasks.discard)


# Every in-flight body read, so a crawl with thousands of responses does not
# pile up thousands of tasks that nothing references.
_api_tasks: "set[Any]" = set()


async def _routes_from_manifest(context: Any, url: str, base_url: str) -> List[str]:
    """Page URLs listed by a sitemap the crawl came across."""
    try:
        response = await context.request.get(url, timeout=MANIFEST_TIMEOUT_MS)
        if not response.ok:
            return []
        text = await response.text()
    except Exception as e:
        print(f"[Worker] manifest fetch failed: {e}", file=sys.stderr, flush=True)
        return []
    # A manifest is a listing, not a page: at most a page or two of it is
    # worth reading, and an unbounded read is how a crawl runs out of time.
    return route_discovery.routes_from_manifest(
        route_discovery.parse_sitemap(text[:MAX_MANIFEST_BYTES]), base_url
    )


def _write_partial(
    output_file: Optional[str],
    pages: List[Dict[str, Any]],
    api: Optional["clone_mock.Capture"] = None,
) -> None:
    """Persist what has been crawled so far.

    The parent kills this process when it overruns its budget, and stdout is
    only written at the very end — without this file a slow site (YouTube)
    loses every page it had already captured.
    """
    if not output_file:
        return
    try:
        tmp = output_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            # The same shape the crawl finishes with, so the parent can read
            # a salvaged file and a complete run the same way.
            json.dump({"pages": pages, "api": api.to_json() if api else {}}, fh)
        os.replace(tmp, output_file)
    except Exception as e:
        print(f"[Worker] Failed to write partial output: {e}", file=sys.stderr, flush=True)


async def crawl(
    start_url: str,
    max_pages: int,
    max_depth: int,
    timeout: int,
    capture_screenshots: bool = False,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Optional[float] = None,
    output_file: Optional[str] = None,
    headless: Optional[bool] = None,
    responsive_flag: bool = False,
    capture_states: bool = False,
) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    # The crawl is strictly observational: load the page, wait it out, scroll
    # to reveal lazy content, capture. `llm_config` is accepted for caller
    # compatibility but deliberately unused — the crawler never clicks, types
    # or submits anything, so the clone describes exactly what a visitor sees
    # on the page itself.

    if headless is None:
        headless = CRAWLER_HEADLESS

    # Own deadline, a little under the parent's subprocess timeout, so the
    # crawl stops cleanly and returns its pages instead of being killed.
    deadline_ts: Optional[float] = (
        time.monotonic() + budget if budget and budget > 0 else None
    )

    def time_left() -> float:
        return float("inf") if deadline_ts is None else deadline_ts - time.monotonic()

    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = "https://" + start_url
        parsed = urlparse(start_url)
    base_url: str = f"{parsed.scheme}://{parsed.netloc}"
    # Heavy SPAs (YouTube, etc.) need more than the default per-page budget.
    goto_timeout: int = max(timeout, 30) * 1000

    visited: set[str] = set()
    pages: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        # Headed by default: Cloudflare-style bot checks flag headless Chromium
        # and serve "Just a moment..." instead of the site. Headless is opt-in
        # (CRAWLER_HEADLESS=1) for machines with no display.
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        # Synchronous in this version of Playwright, and awaited in newer
        # ones. Awaiting it here raised before the crawl started.
        page.set_default_timeout(8000)
        # Installed before the first navigation, so it sees the router's very
        # first push and every request the app makes while it renders.
        await page.add_init_script(route_recorder.ROUTE_RECORDER_JS)
        # Resolved once for the whole crawl: the page is re-laid out at each
        # width in turn for every capture.
        viewports = responsive.responsive(bool(responsive_flag))
        media = MediaCapture()
        # Background requests that came back as a document, and the ones that
        # turned out to be a route manifest.
        document_requests: List[str] = []
        manifest_requests: List[str] = []
        # What the site's own API answered with, kept so the clone can read it.
        api_capture = clone_mock.Capture(base_url=base_url)

        def on_response(response: Any) -> None:
            # Media capture is the first listener's job; this one only looks
            # for routes and data, so it must not handle the body twice.
            media.on_response(response)
            try:
                url = response.url
                if response.request.resource_type not in ("xhr", "fetch", "document"):
                    return
                content_type = response.headers.get("content-type", "")
                route = route_discovery.route_from_xhr(url, base_url, content_type)
                if not route:
                    return
                if route_discovery.is_route_manifest(route):
                    manifest_requests.append(route)
                else:
                    document_requests.append(route)
                # Reading a body is async, and this callback is not: the
                # answer is recorded in the background. The task is held
                # because nothing else keeps it alive, and swept when it
                # finishes, or a long crawl accumulates every response it
                # ever saw.
                _schedule_api_capture(response, api_capture)
            except Exception:
                # A response from a page that navigated away is unreadable.
                pass

        page.on("response", on_response)

        queue = [(start_url, 0)]

        while queue and len(pages) < max_pages:
            if time_left() < 20:
                print(
                    f"[Worker] Time budget spent after {len(pages)} pages, stopping early",
                    file=sys.stderr, flush=True,
                )
                break

            url, depth = queue.pop(0)
            norm = normalize(url)
            if norm in visited or depth > max_depth:
                continue
            visited.add(norm)

            print(f"[Worker] -> {url} (depth={depth})", file=sys.stderr, flush=True)

            try:
                # time_left() is infinite when no budget was given, and
                # int(inf) raises - which used to fail every single goto.
                remaining_ms = (
                    goto_timeout
                    if deadline_ts is None
                    else max(int(time_left() * 1000) - 5000, 5000)
                )
                page_goto_timeout = min(goto_timeout, remaining_ms)
                await page.goto(url, wait_until="domcontentloaded", timeout=page_goto_timeout)
            except Exception as e:
                print(f"[Worker] goto failed: {e}", file=sys.stderr, flush=True)
                continue

            try:
                await page.wait_for_selector("body", timeout=5000)
            except Exception:
                pass

            await page.wait_for_timeout(1500)

            blocked = await wait_out_bot_check(page)

            try:
                txt = await page.inner_text("body")
                if len(txt.strip()) < 100:
                    await page.wait_for_timeout(3000)
            except Exception:
                pass

            await scroll_page(page)

            # Capture the page exactly as it rendered — no clicks, no typing,
            # no navigation. The capture therefore always describes the page
            # that was queued.
            # Downloads are skipped when the crawl is short on time: the
            # pages matter more than the files the network already missed.
            design_data = await collect_design(page)
            favicon = str(design_data.get("meta", {}).get("favicon") or "")
            media_data = await collect_media(
                page,
                context,
                media,
                fetch_missing=time_left() > 60,
                extra_images=[favicon],
            )
            media_data.update(design_data)
            page_data = await _capture_page_data(
                page, url, base_url, depth, capture_screenshots, media_data, viewports
            )
            page_data["blocked"] = blocked
            # Routes the app's own router used while it rendered. A client
            # router that never navigates tells us nothing, which is the case
            # for most pages, so this costs one evaluate and is usually empty.
            recorded = await route_recorder.read_route_recorder(page)
            pushed = route_discovery.record_push_state(recorded["pushes"])
            page_data["pushed_routes"] = pushed

            # The one acting part of the crawl, and only when asked for.
            if capture_states:
                page_data["states"] = await capture_interaction_states(
                    page, url, base_url, interaction_states.MAX_STATES_PER_PAGE
                )
            pages.append(page_data)
            _write_partial(output_file, pages, api_capture)
            print(
                f"[Worker]   {len(pages)}/{max_pages} {page_data['path']} "
                f"links={len(page_data['links'])} media={len(page_data['media'])}",
                file=sys.stderr, flush=True,
            )

            # Queue links from the page for further observation.
            for link in page_data["links"]:
                if link not in visited:
                    queue.append((link, depth + 1))

            # A hash-routed app never requests its other routes, so its own
            # anchors are the only place they are written down.
            for route in page_data.get("hash_routes", []):
                identity = route_discovery.clean(route)
                if identity not in visited:
                    visited.add(identity)
                    queue.append((route, depth + 1))
            # A client router that navigated on its own already showed us the
            # routes it uses.
            for candidate in page_data.get("pushed_routes", []):
                for absolute in route_discovery.merge_discovered(
                    visited, [candidate], base_url, limit=1
                ):
                    queue.append((absolute, depth + 1))
            # A framework that fetched a document asked for a page the crawler
            # has not seen yet.
            for absolute in route_discovery.merge_discovered(
                visited, list(document_requests), base_url
            ):
                queue.append((absolute, depth + 1))

            # A sitemap is the one document that lists a site's own routes.
            for manifest_url in list(manifest_requests):
                manifest_requests.remove(manifest_url)
                routes = await _routes_from_manifest(context, manifest_url, base_url)
                for absolute in route_discovery.merge_discovered(
                    visited, routes, base_url
                ):
                    queue.append((absolute, depth + 1))

        await browser.close()
        # The bodies being read when the crawl ended still have answers in
        # them; waiting for them is the difference between a mock layer with
        # the page's data and one that only caught what came back early.
        if _api_tasks:
            await asyncio.gather(*list(_api_tasks), return_exceptions=True)
        print(f"[Worker] Done. Total pages: {len(pages)}", file=sys.stderr, flush=True)

    return cast(Dict[str, Any], {"pages": pages, "api": api_capture.to_json()})


if __name__ == "__main__":
    data = read_worker_params(sys.argv)
    result = asyncio.run(
        crawl(
            data["url"],
            data["max_pages"],
            data["max_depth"],
            data["timeout"],
            data.get("capture_screenshots", False),
            data.get("llm_config"),
            data.get("budget"),
            data.get("output_file"),
            data.get("headless"),
            data.get("responsive", False),
            data.get("capture_states", False),
        )
    )
    print(json.dumps(result))
