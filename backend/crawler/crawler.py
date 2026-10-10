import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, cast
from urllib.parse import urlparse, urlunparse


@dataclass
class CrawlPage:
    url: str
    path: str
    title: str = ""
    html: str = ""
    screenshot: str = ""
    status_code: int = 200
    links: List[str] = field(default_factory=list[str])
    # Hash-routed pages (`#/pricing`) found on this page. The server never
    # sees them, so they are not in `links`.
    hash_routes: List[str] = field(default_factory=list[str])
    # Routes the page's own router visited while it rendered (pushState).
    pushed_routes: List[str] = field(default_factory=list[str])
    forms: List[Dict[str, Any]] = field(default_factory=list[Dict[str, Any]])
    navigation: List[Dict[str, str]] = field(default_factory=list[Dict[str, str]])
    images: List[str] = field(default_factory=list[str])
    # {"src", "poster"} per <video>; either may be "" when unknown.
    videos: List[Dict[str, str]] = field(default_factory=list[Dict[str, str]])
    # Original media URL -> file name in crawler/media_store.MEDIA_DIR.
    media: Dict[str, str] = field(default_factory=dict[str, str])
    # Computed fonts/colors (bodyFont, headingFont, palette, fontLinks, ...).
    design: Dict[str, Any] = field(default_factory=dict[str, Any])
    # Head metadata: lang, description, ogImage, themeColor, favicon.
    meta: Dict[str, str] = field(default_factory=dict[str, str])
    # Full-page screenshot of the original, a file in the media store.
    screenshot_file: str = ""
    # States the page reveals when its own controls are used; only captured
    # when the user asked for it, since it is the one acting part of a crawl.
    states: List[Dict[str, Any]] = field(default_factory=list[Dict[str, Any]])
    # What lives inside frames and shadow roots, which the page's own markup
    # does not contain.
    embedded: Dict[str, Any] = field(default_factory=dict[str, Any])
    # The same page captured at other widths, keyed by viewport name.
    viewport_screenshots: Dict[str, Dict[str, Any]] = field(default_factory=dict[str, Dict[str, Any]])
    # What the extra widths say about how the page reflows, for the prompt.
    layout_notes: List[str] = field(default_factory=list[str])
    # True when the capture is a bot-check interstitial rather than the site.
    blocked: bool = False
    stylesheets: List[str] = field(default_factory=list[str])
    scripts: List[str] = field(default_factory=list[str])
    depth: int = 0


@dataclass
class CrawlResult:
    base_url: str
    pages: List[CrawlPage] = field(default_factory=list[CrawlPage])
    site_structure: Dict[str, Any] = field(default_factory=dict[str, Any])
    design_tokens: Dict[str, Any] = field(default_factory=dict[str, Any])
    # What the site's own API answered with while it was being crawled, keyed
    # "GET /api/products". A clone has no API behind it, so these are the only
    # record of what the original pages actually loaded.
    api: Dict[str, Any] = field(default_factory=dict[str, Any])
    error: Optional[str] = None


ProgressCallback = Callable[[str, int, int], Coroutine[Any, Any, None]]
# A live frame from the crawl's browser: the page URL and a PNG as a
# data URL, so a caller can show what the crawl is looking at right now.
ScreencastFrameCallback = Callable[[str, str], Coroutine[Any, Any, None]]

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "playwright_worker.py")

# Hard ceiling on a single crawl. Without it a 30-page run on a slow SPA can
# budget over half an hour, and the user watches a frozen progress bar.
MAX_CRAWL_SECONDS = 900

# Slack on top of the crawl's own budget before the process is killed, for a
# browser that has stopped answering. Its own clock is the primary limit; this
# only catches the case where it wedges before noticing.
GRACE_SECONDS = 120


def _run_playwright_subprocess(
    start_url: str,
    max_pages: int,
    max_depth: int,
    timeout: int,
    capture_screenshots: bool = False,
    llm_config: Optional[Dict[str, Any]] = None,
    responsive: bool = False,
    capture_states: bool = False,
    report_progress: Optional[Callable[[int, int], None]] = None,
    report_frame: Optional[Callable[[str, str], None]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """Run Playwright in a separate process.

    Returns `(pages, error)`; a non-None error explains why the worker
    produced nothing, so callers can surface a real reason instead of
    reporting a silent zero-page crawl.
    """
    # LLM-guided exploration is gone: the crawl is strictly observational, so
    # a page needs a goto, a couple of waits and a scroll — nothing more.
    # Capturing a page at three widths triples the screenshot time, so the
    # per-page budget has to grow with it or a responsive crawl is cut off
    # half way through the first page.
    per_page_budget = max(timeout * 3, 90) * (3 if responsive else 1)
    if capture_states:
        # Each state reloads the page before and after, so the capture is the
        # slowest part of the crawl by a wide margin.
        per_page_budget *= 2
    crawl_budget = min(per_page_budget * max_pages, MAX_CRAWL_SECONDS)
    # The worker stops itself first and prints what it has; this is only the
    # backstop for a wedged browser process.
    subprocess_timeout = crawl_budget + GRACE_SECONDS

    # The worker writes its pages here after every capture, so a killed or
    # overrunning crawl still hands back the pages it did get.
    partial_fd, partial_path = tempfile.mkstemp(prefix="crawl_partial_", suffix=".json")
    os.close(partial_fd)

    # The worker streams what its browser is looking at here, one JSON
    # line per frame, so a caller can show the crawl moving instead of
    # a frozen progress bar.
    frames_fd, frames_path = tempfile.mkstemp(prefix="crawl_frames_", suffix=".jsonl")
    os.close(frames_fd)

    params = json.dumps({
        "url": start_url,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "timeout": timeout,
        "capture_screenshots": capture_screenshots,
        "llm_config": llm_config,
        "budget": crawl_budget,
        "output_file": partial_path,
        "responsive": responsive,
        "capture_states": capture_states,
        "screencast_file": frames_path,
    })

    def read_partial() -> List[Dict[str, Any]]:
        try:
            with open(partial_path, "r", encoding="utf-8") as fh:
                data: Any = json.load(fh)
        except Exception:
            return []
        if isinstance(data, dict):
            data = cast(Dict[str, Any], data).get("pages")
        if not isinstance(data, list):
            return []
        return [item for item in cast(List[Any], data) if isinstance(item, dict)]

    def split(payload: Any) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Pages and captured API from whatever the worker printed.

        A bare list is the older shape and still accepted: a partially written
        file from an interrupted crawl is a list, and those pages are still
        worth keeping even though no answers came with them.
        """
        if isinstance(payload, dict):
            found = cast(Dict[str, Any], payload)
            raw_pages = found.get("pages")
            raw_api = found.get("api")
            pages: List[Dict[str, Any]] = [
                cast(Dict[str, Any], item)
                for item in cast(List[Any], raw_pages or [])
                if isinstance(item, dict)
            ]
            api: Dict[str, Any] = cast(Dict[str, Any], raw_api) if isinstance(raw_api, dict) else {}
            return pages, api
        if isinstance(payload, list):
            return [item for item in cast(List[Any], payload) if isinstance(item, dict)], {}
        return [], {}

    # The worker records each page in the partial file as it captures it, and
    # nowhere else until it finishes. Watching that file is the only way to
    # tell somebody the crawl is still moving - without it a run sits on
    # "Starting crawl" for minutes showing 0 of 30, which from the outside is
    # indistinguishable from a hang. That is not a cosmetic problem: people
    # reload a page that looks stuck and pay for it twice.
    last_reported = 0

    def publish_if_moved() -> None:
        nonlocal last_reported
        if report_progress is None:
            return
        found = len(read_partial())
        if found > last_reported:
            last_reported = found
            report_progress(found, max_pages)

    # Frames are appended as the crawl runs, so the file is read from
    # the byte offset it was last read at; a frame is one JSON line.
    frames_offset = 0

    def publish_frames() -> None:
        nonlocal frames_offset
        if report_frame is None:
            return
        try:
            with open(frames_path, "r", encoding="utf-8") as fh:
                fh.seek(frames_offset)
                chunk = fh.read()
                frames_offset += len(chunk)
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            url = frame.get("url")
            image = frame.get("image")
            if isinstance(url, str) and isinstance(image, str) and image:
                report_frame(url, image)

    try:
        # Parameters go in over stdin: they carry the API key, and a command
        # line is readable by every process on the machine.
        # The frames file goes over the environment rather than
        # the command line, which every process can read.
        worker_env = os.environ.copy()
        worker_env["CRAWLER_SCREENCAST_FILE"] = frames_path
        process = subprocess.Popen(
            [sys.executable, WORKER_SCRIPT, "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=worker_env,
        )
        # Declared as Optional because Popen allows a process with no stdin;
        # this one was asked for a pipe, so it has one.
        worker_stdin = process.stdin
        assert worker_stdin is not None
        worker_stdin.write(params)
        # Closed rather than left open: the worker reads to end of input, and
        # a pipe held open is a worker waiting for parameters that will never
        # come.
        worker_stdin.close()

        # Both pipes are drained on their own threads. Waiting without
        # reading would deadlock: a responsive crawl writes megabytes of
        # base64 screenshots to stdout, that fills the pipe buffer, and the
        # worker blocks on a write while the parent blocks on a wait.
        collected: Dict[str, List[str]] = {"out": [], "err": []}

        def drain(stream: Any, key: str) -> None:
            try:
                if key == "err":
                    # Logged as it arrives: the worker's account of a launch
                    # that never finishes is otherwise invisible until the
                    # subprocess timeout, and the log shows a crawl that
                    # started and said nothing.
                    for line in stream:
                        collected[key].append(line)
                        print(f"[Crawler] {line.rstrip()}", flush=True)
                    return
                text = stream.read()
            except Exception:
                text = ""
            collected[key].append(text or "")

        readers = [
            threading.Thread(target=drain, args=(process.stdout, "out"), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, "err"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + subprocess_timeout
        expired = False
        while True:
            try:
                process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                publish_if_moved()
                publish_frames()
                if time.monotonic() >= deadline:
                    expired = True
                    process.kill()
                    break

        for reader in readers:
            reader.join(timeout=5)
        publish_if_moved()
        publish_frames()
        stdout = "".join(collected["out"])
        stderr = "".join(collected["err"])

        if expired:
            # Same as the timeout this replaces: the browser is wedged, and
            # whatever the crawl managed to capture is still worth keeping.
            process.wait(timeout=10)

        if process.returncode != 0 and not expired:
            salvaged = read_partial()
            if salvaged:
                print(f"[Crawler] Worker crashed; using {len(salvaged)} salvaged pages")
                return salvaged, {}, None
            lines = (stderr or stdout).strip().splitlines()
            reason = lines[-1][:300] if lines else "no output"
            return [], {}, f"Playwright worker exited with code {process.returncode}: {reason}"
        if expired:
            salvaged = read_partial()
            if salvaged:
                print(f"[Crawler] Worker timed out; using {len(salvaged)} salvaged pages")
                return salvaged, {}, None
            return [], {}, (
                "The site took too long to crawl and no page finished loading. "
                "Try a smaller page limit, or a site that loads faster."
            )
        pages, api = split(json.loads(stdout))
        return pages, api, None
    except subprocess.TimeoutExpired:
        salvaged = read_partial()
        if salvaged:
            print(f"[Crawler] Worker timed out; using {len(salvaged)} salvaged pages")
            return salvaged, {}, None
        return [], {}, (
            "The site took too long to crawl and no page finished loading. "
            "Try a smaller page limit, or a site that loads faster."
        )
    except (ValueError, KeyError) as e:
        salvaged = read_partial()
        if salvaged:
            return salvaged, {}, None
        return [], {}, f"Playwright worker returned invalid data: {e}"
    except Exception as e:
        return [], {}, f"Playwright subprocess error: {e}"
    finally:
        try:
            os.remove(partial_path)
        except OSError:
            pass
        try:
            os.remove(frames_path)
        except OSError:
            pass


class SiteCrawler:
    def __init__(
        self,
        max_pages: int = 30,
        max_depth: int = 4,
        timeout: int = 15,
        capture_screenshots: bool = False,
        llm_config: Optional[Dict[str, Any]] = None,
        responsive: bool = False,
        capture_states: bool = False,
    ):
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout
        # Screenshots are base64 PNGs, ~0.5-2 MB per page. The url-to-code
        # path never sends them to a model, so capturing them by default just
        # burns crawl time and memory.
        self.capture_screenshots = capture_screenshots
        # Capture each page at phone, tablet and desktop widths, so the clone
        # is built from what visitors actually see rather than from the
        # desktop layout alone. Off unless asked for: it triples capture time.
        self.responsive = responsive
        # Click the page's own controls and photograph what they reveal. The
        # only part of the crawl that acts rather than watches, so it has to
        # be asked for by name.
        self.capture_states = capture_states
        # Accepted for caller compatibility; the crawl never clicks, types or
        # submits anything, so no model is consulted during exploration.
        self.llm_config = llm_config
        self._progress_callback: Optional[ProgressCallback] = None
        self._frame_callback: Optional[ScreencastFrameCallback] = None

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        self._progress_callback = callback

    def set_frame_callback(self, callback: ScreencastFrameCallback) -> None:
        self._frame_callback = callback

    async def _report_progress(self, status: str, current: int, total: int) -> None:
        if self._progress_callback:
            await self._progress_callback(status, current, total)

    async def _report_frame(self, url: str, image: str) -> None:
        if self._frame_callback:
            await self._frame_callback(url, image)

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", parsed.query, "")
        )

    def _is_same_domain(self, url: str, base_url: str) -> bool:
        url_domain = urlparse(url).netloc
        base_domain = urlparse(base_url).netloc
        return url_domain == base_domain or url_domain.endswith("." + base_domain)

    def _extract_path(self, url: str, base_url: str) -> str:
        base_path = urlparse(base_url).path.rstrip("/")
        url_path = urlparse(url).path
        if base_path and url_path.startswith(base_path):
            return url_path[len(base_path) :] or "/"
        return url_path

    async def crawl(self, start_url: str) -> CrawlResult:
        parsed = urlparse(start_url)
        if not parsed.scheme:
            start_url = f"https://{start_url}"
            parsed = urlparse(start_url)

        base_url = f"{parsed.scheme}://{parsed.netloc}"

        await self._report_progress("Starting crawl (observe only)...", 0, self.max_pages)

        # The crawl runs on a worker thread, which cannot await. Schedules the
        # callback back onto the loop instead, so the counter a person is
        # watching actually moves while the browser is working.
        loop = asyncio.get_event_loop()

        def report_from_thread(current: int, total: int) -> None:
            asyncio.run_coroutine_threadsafe(
                self._report_progress(f"Read {current} page(s)...", current, total),
                loop,
            )

        def report_frame_from_thread(url: str, image: str) -> None:
            asyncio.run_coroutine_threadsafe(self._report_frame(url, image), loop)

        try:
            page_dicts, api, worker_error = await asyncio.get_event_loop().run_in_executor(
                None,
                _run_playwright_subprocess,
                start_url,
                self.max_pages,
                self.max_depth,
                self.timeout,
                self.capture_screenshots,
                self.llm_config,
                self.responsive,
                self.capture_states,
                report_from_thread,
                report_frame_from_thread,
            )
        except Exception as e:
            return CrawlResult(base_url=base_url, error=f"Crawl failed: {e}")

        if worker_error:
            return CrawlResult(base_url=base_url, error=worker_error)

        pages: List[CrawlPage] = []
        for pd in page_dicts:
            pages.append(CrawlPage(
                url=pd["url"],
                path=pd["path"],
                title=pd["title"],
                html=pd["html"],
                screenshot=pd.get("screenshot", ""),
                links=pd.get("links", []),
                hash_routes=pd.get("hash_routes", []),
                pushed_routes=pd.get("pushed_routes", []),
                viewport_screenshots=pd.get("viewport_screenshots", {}),
                layout_notes=pd.get("layout_notes", []),
                states=pd.get("states", []),
                embedded=pd.get("embedded", {}),
                forms=pd.get("forms", []),
                navigation=pd.get("navigation", []),
                images=pd.get("images", []),
                videos=pd.get("videos", []),
                media=pd.get("media", {}),
                design=pd.get("design", {}),
                meta=pd.get("meta", {}),
                screenshot_file=pd.get("screenshot_file", ""),
                blocked=bool(pd.get("blocked", False)),
                depth=pd.get("depth", 0),
            ))

        await self._report_progress(
            f"Crawl complete. Found {len(pages)} pages.",
            len(pages),
            len(pages),
        )

        site_structure = self._build_site_structure(pages, base_url)
        design_tokens = self._extract_design_tokens(pages)

        if api:
            print(f"[Crawler] Captured {len(api)} API responses", flush=True)

        if not pages:
            # Zero pages with no error reported is not a success: every
            # navigation failed and the worker had nothing to say about it.
            # Reported as a failure so the caller stops here, rather than
            # paying a model to invent a page for a site that was never
            # reached.
            return CrawlResult(
                base_url=base_url,
                error=(
                    f"Could not load {base_url}. The site did not respond - the "
                    "address may be wrong, or the site may be down or refusing "
                    "connections."
                ),
            )

        return CrawlResult(
            base_url=base_url,
            pages=pages,
            site_structure=site_structure,
            design_tokens=design_tokens,
            api=api,
        )

    def _build_site_structure(self, pages: List[CrawlPage], base_url: str) -> Dict[str, Any]:
        structure: Dict[str, Any] = {"pages": [], "navigation": {}}
        for page in pages:
            structure["pages"].append({
                "path": page.path,
                "title": page.title,
                "url": page.url,
                "depth": page.depth,
                "has_forms": len(page.forms) > 0,
                "form_count": len(page.forms),
                "images_count": len(page.images),
            })
        if pages:
            structure["navigation"] = {"items": pages[0].navigation[:20]}
        return structure

    def _extract_design_tokens(self, pages: List[CrawlPage]) -> Dict[str, Any]:
        """Site-wide fonts and colors.

        The browser's computed styles say what the site really renders with;
        scraping the markup for "color:" only sees inline styles, so it is
        the fallback for captures without them.
        """
        rendered = next((page.design for page in pages if page.design), None)
        if rendered:
            fonts = [
                font
                for font in (rendered.get("bodyFont"), rendered.get("headingFont"))
                if font
            ]
            return {
                "colors": list(rendered.get("palette", []))[:20],
                "fonts": list(dict.fromkeys(fonts)),
                "fontLinks": list(rendered.get("fontLinks", [])),
            }

        colors: List[str] = []
        fonts: List[str] = []
        for page in pages[:5]:
            color_matches = re.findall(
                r"(?:color|background-color|border-color)\s*:\s*([^;]+)", page.html
            )
            colors.extend(c.strip() for c in color_matches if c.strip())
            font_matches = re.findall(r"family=([^&]+)", page.html)
            for fm in font_matches:
                fonts.append(fm.replace("+", " ").split(":")[0])
        return {
            "colors": list(set(colors))[:20],
            "fonts": list(set(fonts))[:10],
        }
