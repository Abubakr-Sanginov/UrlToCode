"""Study a page URL the user pastes into the update chat.

Scenario: a site was cloned from domain.com and the user then sends
"domain.com/login" in the chat. The crawler fetches that one page (observe
only — no clicks, no typing), and the captured structure is attached to the
update prompt so the agent can study it and wire the matching control (e.g. a
"Sign in" button) on the current page to the captured page's content.
"""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
from typing import List

from crawler.crawler import SiteCrawler
from prompts.html_compact import compact_html
from prompts.url_to_code_prompts import page_filename

# A page fetch is capped hard: this runs inline in the prompt pipeline, and a
# wedged site must not stall the update for minutes.
FETCH_TIMEOUT_SECONDS = 90
PAGE_LOAD_TIMEOUT = 15

MAX_ATTACHED_PAGES = 2
# Per-page structural budget. Enough to see a login/pricing page whole; the
# attached page follows the same fidelity contract as the rest of the clone.
ATTACHED_PAGE_BUDGET = 6000

# Full URLs plus bare domains ("domain.com/login"). Bare-domain matching
# requires 2+ chars before the dot so "e.g." and version strings don't match.
_URL_RE = re.compile(
    r"(?:(?:https?|ftp)://[^\s<>\"')\]]+|(?:www\.)?[A-Za-z0-9][A-Za-z0-9-]{1,61}"
    r"\.[A-Za-z]{2,}(?:/[^\s<>\"')\]]*)?)",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,;:!?"


@dataclass
class AttachedPage:
    url: str
    path: str
    title: str
    filename: str
    structure: str


def _normalize_url(raw: str) -> str:
    url = raw.strip().strip(_TRAILING_PUNCT + "\"')")
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = "https://" + url
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def find_page_urls(text: str) -> List[str]:
    """Page URLs the user referenced, deduplicated, most recent context kept."""
    urls: List[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text or ""):
        url = _normalize_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= MAX_ATTACHED_PAGES:
            break
    return urls


async def _fetch_one(url: str) -> AttachedPage | None:
    crawler = SiteCrawler(max_pages=1, max_depth=1, timeout=PAGE_LOAD_TIMEOUT)
    result = await asyncio.wait_for(crawler.crawl(url), timeout=FETCH_TIMEOUT_SECONDS)
    page = next((p for p in result.pages if not p.blocked and p.html.strip()), None)
    if page is None:
        return None
    return AttachedPage(
        url=page.url,
        path=page.path,
        title=page.title or page.path,
        filename=page_filename(page.path),
        structure=compact_html(page.html, max_chars=ATTACHED_PAGE_BUDGET),
    )


async def fetch_attached_pages(text: str) -> List[AttachedPage]:
    """Fetch every page URL in the user's text, passively.

    Best-effort by design: a fetch that fails or times out is skipped, and the
    update proceeds as a normal edit instead of failing the request.
    """
    pages: List[AttachedPage] = []
    for url in find_page_urls(text):
        try:
            page = await _fetch_one(url)
        except Exception as e:
            print(f"[AttachedPage] Fetch failed for {url}: {e}")
            continue
        if page is None:
            print(f"[AttachedPage] Nothing usable captured for {url}")
            continue
        pages.append(page)
    return pages


def build_attached_pages_block(pages: List[AttachedPage]) -> str:
    """Prompt block that hands the captured page(s) to the editing agent."""
    if not pages:
        return ""

    page_blocks: List[str] = []
    for page in pages:
        page_blocks.append(
            f'<attached_page url="{page.url}" path="{page.path}" '
            f'title="{page.title}" local_file="{page.filename}">\n'
            f"{page.structure}\n"
            f"</attached_page>"
        )
    pages_text = "\n\n".join(page_blocks)

    return f"""<attached_pages>
The user referenced a page URL in this request. That page was fetched (read
only) and its captured structure is below.

STUDY the attached page, then update the current page so the two are connected:

1. Work out what the attached page is (login, sign-up, pricing, docs, ...).
2. Find the control in the current page that leads to it: a button, link or
   menu item whose visible text or purpose matches (e.g. "Sign in", "Войти",
   "Pricing"). Match by meaning, not by exact string.
3. Wire that control to open the attached page's content inside the current
   page:
   - If the current page keeps views in <section data-page="..."> blocks with
     a switching script (the single-file clone convention), reproduce the
     attached page as one more hidden section (use path="{pages[0].path}") and
     make the control switch to it.
   - Otherwise, reproduce the attached page as a hidden overlay/section in the
     current file and make the control reveal it with a small inline script;
     also point the control's href at "{pages[0].filename}" so a cloned
     multi-page project navigates natively.
4. The reproduction must be a 1:1 copy of the attached page — same layout,
   same text, same colors and fonts as captured. Do not redesign it, and do
   not change anything else in the current page.

If the current page has no control that could lead to the attached page, say
so in chat instead of inventing one.
</attached_pages>

{pages_text}"""
