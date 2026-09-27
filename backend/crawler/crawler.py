import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse, urlunparse


@dataclass
class CrawlPage:
    url: str
    path: str
    title: str = ""
    html: str = ""
    screenshot: str = ""
    status_code: int = 200
    links: List[str] = field(default_factory=list)
    forms: List[Dict[str, Any]] = field(default_factory=list)
    navigation: List[Dict[str, str]] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    stylesheets: List[str] = field(default_factory=list)
    scripts: List[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class CrawlResult:
    base_url: str
    pages: List[CrawlPage] = field(default_factory=list)
    site_structure: Dict[str, Any] = field(default_factory=dict)
    design_tokens: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


ProgressCallback = Callable[[str, int, int], Coroutine[Any, Any, None]]

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "playwright_worker.py")


def _run_playwright_subprocess(
    start_url: str,
    max_pages: int,
    max_depth: int,
    timeout: int,
) -> List[Dict[str, Any]]:
    """Run Playwright in a completely separate Python process via subprocess."""
    params = json.dumps({
        "url": start_url,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "timeout": timeout,
    })

    try:
        result = subprocess.run(
            [sys.executable, WORKER_SCRIPT, params],
            capture_output=True,
            text=True,
            timeout=min(timeout * max_pages + 60, 300),
        )
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                print(f"[Crawler] {line}")
        if result.returncode != 0:
            print(f"[Crawler] Playwright stderr: {result.stderr[:500]}")
            return []
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("[Crawler] Playwright subprocess timed out")
        return []
    except Exception as e:
        print(f"[Crawler] Playwright subprocess error: {e}")
        return []


class SiteCrawler:
    def __init__(self, max_pages: int = 30, max_depth: int = 4, timeout: int = 15):
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout
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
            page_dicts = await asyncio.get_event_loop().run_in_executor(
                None,
                _run_playwright_subprocess,
                start_url,
                self.max_pages,
                self.max_depth,
                self.timeout,
            )
        except Exception as e:
            return CrawlResult(base_url=base_url, error=f"Crawl failed: {e}")

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
