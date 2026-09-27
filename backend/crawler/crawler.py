import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
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
    forms: List[Dict[str, Any]] = field(default_factory=list[Dict[str, Any]])
    navigation: List[Dict[str, str]] = field(default_factory=list[Dict[str, str]])
    images: List[str] = field(default_factory=list[str])
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
    error: Optional[str] = None


ProgressCallback = Callable[[str, int, int], Coroutine[Any, Any, None]]

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "playwright_worker.py")

# Hard ceiling on a single crawl. Without it a 30-page run on a slow SPA can
# budget over half an hour, and the user watches a frozen progress bar.
MAX_CRAWL_SECONDS = 900


def _run_playwright_subprocess(
    start_url: str,
    max_pages: int,
    max_depth: int,
    timeout: int,
    capture_screenshots: bool = False,
    llm_config: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Run Playwright in a separate process.

    Returns `(pages, error)`; a non-None error explains why the worker
    produced nothing, so callers can surface a real reason instead of
    reporting a silent zero-page crawl.
    """
    # LLM-guided exploration adds a model call plus several interactions per
    # shallow page, which can take 30-90s on slow endpoints. Budget for it so
    # heavy SPAs like YouTube do not get killed mid-exploration.
    per_page_budget = max(timeout * 3, 90)
    if llm_config:
        per_page_budget += 90
    crawl_budget = min(per_page_budget * max_pages, MAX_CRAWL_SECONDS)
    # The worker stops itself first and prints what it has; this is only the
    # backstop for a wedged browser process.
    subprocess_timeout = crawl_budget + 120

    # The worker writes its pages here after every capture, so a killed or
    # overrunning crawl still hands back the pages it did get.
    partial_fd, partial_path = tempfile.mkstemp(prefix="crawl_partial_", suffix=".json")
    os.close(partial_fd)

    params = json.dumps({
        "url": start_url,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "timeout": timeout,
        "capture_screenshots": capture_screenshots,
        "llm_config": llm_config,
        "budget": crawl_budget,
        "output_file": partial_path,
    })

    def read_partial() -> List[Dict[str, Any]]:
        try:
            with open(partial_path, "r", encoding="utf-8") as fh:
                data: Any = json.load(fh)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in cast(List[Any], data) if isinstance(item, dict)]

    try:
        # Parameters go in over stdin: they carry the API key, and a command
        # line is readable by every process on the machine.
        result = subprocess.run(
            [sys.executable, WORKER_SCRIPT, "-"],
            input=params,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                print(f"[Crawler] {line}")
        if result.returncode != 0:
            salvaged = read_partial()
            if salvaged:
                print(f"[Crawler] Worker crashed; using {len(salvaged)} salvaged pages")
                return salvaged, None
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            reason = lines[-1][:300] if lines else "no output"
            return [], f"Playwright worker exited with code {result.returncode}: {reason}"
        return json.loads(result.stdout), None
    except subprocess.TimeoutExpired:
        salvaged = read_partial()
        if salvaged:
            print(f"[Crawler] Worker timed out; using {len(salvaged)} salvaged pages")
            return salvaged, None
        return [], (
            "The site took too long to crawl and no page finished loading. "
            "Try a smaller page limit, or a site that loads faster."
        )
    except (ValueError, KeyError) as e:
        salvaged = read_partial()
        if salvaged:
            return salvaged, None
        return [], f"Playwright worker returned invalid data: {e}"
    except Exception as e:
        return [], f"Playwright subprocess error: {e}"
    finally:
        try:
            os.remove(partial_path)
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
    ):
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout
        # Screenshots are base64 PNGs, ~0.5-2 MB per page. The url-to-code
        # path never sends them to a model, so capturing them by default just
        # burns crawl time and memory.
        self.capture_screenshots = capture_screenshots
        # If provided, the crawler asks the model what to interact with on
        # each shallow page (search boxes, tabs, accordions) and captures the
        # resulting content — the AI "looks" at the page instead of blindly
        # clicking a fixed selector list.
        self.llm_config = llm_config
        self._progress_callback: Optional[ProgressCallback] = None

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        self._progress_callback = callback

    async def _report_progress(self, status: str, current: int, total: int) -> None:
        if self._progress_callback:
            await self._progress_callback(status, current, total)

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

        await self._report_progress("Starting interactive crawl...", 0, self.max_pages)

        try:
            page_dicts, worker_error = await asyncio.get_event_loop().run_in_executor(
                None,
                _run_playwright_subprocess,
                start_url,
                self.max_pages,
                self.max_depth,
                self.timeout,
                self.capture_screenshots,
                self.llm_config,
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
                forms=pd.get("forms", []),
                navigation=pd.get("navigation", []),
                images=pd.get("images", []),
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

        return CrawlResult(
            base_url=base_url,
            pages=pages,
            site_structure=site_structure,
            design_tokens=design_tokens,
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
