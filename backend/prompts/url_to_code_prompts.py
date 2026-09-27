import re
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

from crawler.crawler import CrawlResult
from prompts.html_compact import UrlMapper, compact_html

# Per-page structural markup budget, in characters. The compactor typically
# reduces real pages 10-150x, so this holds a whole page skeleton rather than
# an arbitrary head-slice of raw markup.
PAGE_HTML_BUDGET = 5000

MAX_NAV_ITEMS = 8
MAX_FORMS = 2
MAX_FORM_INPUTS = 6
MAX_PAGES_LISTED = 20
MAX_IMAGES = 12
MAX_IMAGE_URL_LEN = 300

INDEX_FILENAME = "index.html"

# Per-stack CDN/output contract. Keyed by the `Stack` values the frontend
# sends (frontend/src/lib/stacks.ts). Without this the selected stack was
# accepted by the API and then ignored, so every run emitted HTML+Tailwind.
_STACK_RULES: Dict[str, str] = {
    "html_tailwind": (
        "- Tailwind via CDN: <script src=\"https://cdn.tailwindcss.com\"></script>\n"
        "- Alpine.js for interactivity: <script defer src=\"https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js\"></script>"
    ),
    "html_css": (
        "- No CSS framework. Write all styles by hand in a single <style> block.\n"
        "- Vanilla JS only, in a single <script> block."
    ),
    "react_tailwind": (
        "- React 18 + Babel via CDN (react, react-dom, @babel/standalone).\n"
        "- Put the app in <script type=\"text/babel\"> and mount it into <div id=\"root\">.\n"
        "- Tailwind via CDN: <script src=\"https://cdn.tailwindcss.com\"></script>"
    ),
    "bootstrap": (
        "- Bootstrap 5 CSS and JS bundle via CDN. Use Bootstrap components and grid classes.\n"
        "- No Tailwind."
    ),
    "vue_tailwind": (
        "- Vue 3 global build via CDN (unpkg.com/vue@3/dist/vue.global.js).\n"
        "- Mount the app into <div id=\"app\"> with Vue.createApp.\n"
        "- Tailwind via CDN: <script src=\"https://cdn.tailwindcss.com\"></script>"
    ),
    "ionic_tailwind": (
        "- Ionic Framework web components via CDN plus Tailwind via CDN.\n"
        "- Use ion-* components for chrome (ion-app, ion-header, ion-content)."
    ),
}

_DEFAULT_STACK = "html_tailwind"


def _stack_rules(stack: str) -> str:
    return _STACK_RULES.get(stack, _STACK_RULES[_DEFAULT_STACK])


def _output_rules(stack: str) -> str:
    """Output contract for one generated file, specialised per stack."""
    return f"""OUTPUT RULES:
- Emit ONE complete HTML file, nothing else. Start at <!DOCTYPE html>.
- No markdown fences, no commentary, no create_file directives.
{_stack_rules(stack)}
- Icons: lucide via CDN (unpkg.com/lucide@latest).
- Images: use the original image URLs from the source exactly as given (they are
  absolute). Use https://placehold.co/600x400?text=Image only where the source
  has no image URL.
- Links: keep every href exactly as given in the source. Crawled pages already
  point at their local .html files; other links are absolute URLs. Use # only
  when no href is known.
- Keep all CSS and JS inline in the file. Responsive."""


def page_filename(path: str) -> str:
    """Local file a crawled page is saved under in the downloaded project.

    Mirrors `buildProjectFiles` in frontend/src/lib/localProject.ts, so links
    written into one generated page open the right sibling file.
    """
    cleaned = (path or "").strip()
    if cleaned in ("", "/"):
        return INDEX_FILENAME
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    return f"{slug or 'page'}.html"


def _normalize_path(path: str) -> str:
    return path.rstrip("/") or "/"


def build_link_map(paths: Iterable[str]) -> Dict[str, str]:
    """Map every crawled page path to its local file (first one wins)."""
    link_map: Dict[str, str] = {}
    for path in paths:
        link_map.setdefault(_normalize_path(path), page_filename(path))
    return link_map


def _host(netloc: str) -> str:
    netloc = netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def make_url_mapper(page_url: str, link_map: Dict[str, str] | None = None) -> UrlMapper:
    """Make relative URLs absolute and send crawled pages to their local files.

    A clone saved to disk resolves "/logo.png" against itself, so every
    relative image was broken and every internal link left the clone.
    """
    base_host = _host(urlparse(page_url).netloc)

    def mapper(attr: str, value: str) -> str:
        value = value.strip()
        if not page_url or not value or value.startswith(
            ("#", "data:", "mailto:", "tel:", "javascript:")
        ):
            return value
        absolute = urljoin(page_url, value)
        if attr != "href" or not link_map:
            return absolute
        target = urlparse(absolute)
        if _host(target.netloc) != base_host:
            return absolute
        path = _normalize_path(target.path)
        local = (target.query and link_map.get(f"{path}?{target.query}")) or link_map.get(path)
        if not local:
            return absolute
        return f"{local}#{target.fragment}" if target.fragment else local

    return mapper


def _format_images(images: List[str], page_url: str) -> str:
    urls: List[str] = []
    for src in images:
        absolute = urljoin(page_url, src.strip()) if page_url else src.strip()
        if (
            not absolute
            or absolute.startswith("data:")
            or len(absolute) > MAX_IMAGE_URL_LEN
            or absolute in urls
        ):
            continue
        urls.append(absolute)
        if len(urls) >= MAX_IMAGES:
            break
    if not urls:
        return ""
    return "\nIMAGES (original URLs, use them as-is):\n" + "\n".join(
        f"- {u}" for u in urls
    )


def _format_links(link_map: Dict[str, str] | None) -> str:
    if not link_map:
        return ""
    lines = [
        f"- {path} -> {filename}"
        for path, filename in list(link_map.items())[:MAX_PAGES_LISTED]
    ]
    return "\nLOCAL PAGES (link to these files, not the original site):\n" + "\n".join(
        lines
    )


def _format_nav(
    navigation: List[Dict[str, str]], url_mapper: UrlMapper | None = None
) -> str:
    items = [
        f'{item.get("text", "").strip()} -> '
        f'{url_mapper("href", item.get("href", "")) if url_mapper else item.get("href", "")}'
        for item in navigation[:MAX_NAV_ITEMS]
        if item.get("text")
    ]
    return "; ".join(items) if items else "none"


def _format_forms(forms: List[Dict[str, Any]]) -> str:
    if not forms:
        return ""

    lines: List[str] = []
    for form in forms[:MAX_FORMS]:
        fields = ", ".join(
            f'{inp.get("name") or "unnamed"}:{inp.get("type", "text")}'
            for inp in form.get("inputs", [])[:MAX_FORM_INPUTS]
        )
        method = form.get("method", "GET")
        action = form.get("action") or "-"
        lines.append(f"- {method} {action} [{fields}]")

    return "\nFORMS:\n" + "\n".join(lines)


def build_page_prompt(
    page_data: Dict[str, Any],
    stack: str,
    generate_database: bool = True,
    link_map: Dict[str, str] | None = None,
) -> List[Dict[str, str]]:
    page_path = page_data.get("path", "/")
    page_title = page_data.get("title", "Page")
    page_url = page_data.get("url", "")
    url_mapper = make_url_mapper(page_url, link_map)
    structure = compact_html(
        page_data.get("html", ""), max_chars=PAGE_HTML_BUDGET, url_mapper=url_mapper
    )

    system_prompt = f"""Copy this page as a single self-contained HTML file. Reproduce the original EXACTLY — same layout, same colors, same fonts, same spacing, same text. Do NOT improve, redesign, or modernize anything.

FIDELITY RULES (highest priority — violation = failure):
- Copy the EXACT layout, structure, and visual appearance from STRUCTURE below.
- Reproduce EXACTLY the visible text from STRUCTURE below. Every heading, label and paragraph must come from it — character for character.
- Match colors, fonts, spacing, borders, shadows as closely as possible to the original.
- Do NOT invent sections, statistics, features or copy that are not in STRUCTURE.
- Do NOT add extra decoration, animations, gradients or visual polish not present in the original.
- Preserve the original document language (do not translate).

PAGE: {page_path} - {page_title}
NAV: {_format_nav(page_data.get("navigation", []), url_mapper)}{_format_forms(page_data.get("forms", []))}{_format_images(page_data.get("images", []), page_url)}{_format_links(link_map)}

STRUCTURE (compacted; "+N similar items" means repeat that block N more times):
{structure}

{_output_rules(stack)}"""

    return [{"role": "system", "content": system_prompt}]


def build_single_file_prompt(
    crawl_result: CrawlResult,
    stack: str,
    per_page_budget: int = 3500,
    max_pages: int = 5,
) -> str:
    """One LLM call that packs every crawled page into a single HTML file.

    Each page becomes a <section data-page="...">; the nav switches between
    them client-side. Cheaper and self-contained for users who do not want a
    project folder.
    """
    pages_block: List[str] = []
    for page in crawl_result.pages[:max_pages]:
        structure = compact_html(
            page.html, max_chars=per_page_budget, url_mapper=make_url_mapper(page.url)
        )
        pages_block.append(
            f'=== PAGE "{page.path}" (nav label: {page.title}) ===\n{structure}'
        )
    pages_text = "\n\n".join(pages_block)

    nav_items: List[Dict[str, str]] = []
    for page in crawl_result.pages:
        nav_items.extend(page.navigation)
        if len(nav_items) >= MAX_NAV_ITEMS:
            break

    colors = ", ".join(crawl_result.design_tokens.get("colors", [])[:8]) or "none"
    fonts = ", ".join(crawl_result.design_tokens.get("fonts", [])[:4]) or "none"

    return f"""Pack this website into ONE self-contained index.html.

FIDELITY RULES (highest priority — violation = failure):
- Copy the EXACT layout, structure, and visual appearance from the PAGE blocks below.
- Reproduce ONLY the visible text and structure from the source — character for character.
- Do NOT invent sections, statistics or copy that is not in the source.
- Do NOT add extra decoration, animations, gradients or visual polish not present in the original.
- Preserve the original document language (do not translate).

STRUCTURE CONTRACT:
- Every page goes into <section data-page="/path">...</section>. The first
  page is visible by default, all others hidden.
- A fixed nav renders one entry per page; clicking an entry shows its
  section (display toggle via a small inline <script>, no frameworks).
- Active nav entry is highlighted. Keep per-page internal layout intact.

PAGES:
{pages_text}

NAV (merge with page navs where sensible): {_format_nav(nav_items)}
COLORS: {colors}
FONTS: {fonts}

{_output_rules(stack)}"""


def build_database_schema_prompt(
    crawl_result: CrawlResult,
    stack: str,
) -> str:
    listed_pages = crawl_result.pages[:MAX_PAGES_LISTED]

    forms_summary: List[str] = []
    for page in listed_pages:
        for form in page.forms[:MAX_FORMS]:
            inputs = ", ".join(
                f'{inp.get("name") or "unnamed"}:{inp.get("type", "text")}'
                for inp in form.get("inputs", [])[:MAX_FORM_INPUTS]
            )
            forms_summary.append(
                f"- {page.path} {form.get('method', 'GET')} [{inputs}]"
            )

    pages_list = "\n".join(f"- {page.path} ({page.title})" for page in listed_pages)
    forms_list = "\n".join(forms_summary) if forms_summary else "none"

    return f"""Design a database schema for this site.

PAGES:
{pages_list}

FORMS:
{forms_list}

Return only SQL CREATE TABLE statements. No fences, no prose. Start at CREATE TABLE."""


def build_project_structure_prompt(
    crawl_result: CrawlResult,
    stack: str,
    generate_database: bool = True,
    generate_auth: bool = True,
) -> str:
    pages_list = "\n".join(
        f"- {page.path} ({page.title}) d{page.depth}"
        for page in crawl_result.pages[:MAX_PAGES_LISTED]
    )

    nav_items: List[Dict[str, str]] = []
    for page in crawl_result.pages:
        nav_items.extend(page.navigation)
        if len(nav_items) >= MAX_NAV_ITEMS:
            break

    colors = ", ".join(crawl_result.design_tokens.get("colors", [])[:8]) or "none"
    fonts = ", ".join(crawl_result.design_tokens.get("fonts", [])[:4]) or "none"

    requirements = [
        "- Nav menu linking to every page listed below, using its local .html file name.",
        "- Responsive card grid of the pages.",
        "- Styling that matches the original site.",
    ]
    if generate_auth:
        requirements.append(
            "- Sign in / sign up controls in the header, plus a working modal "
            "with email and password fields (client-side only)."
        )
    if generate_database:
        requirements.append(
            "- A short 'Data model' section naming the main entities the site implies."
        )

    requirements_block = "\n".join(requirements)

    return f"""Generate a single self-contained index.html that acts as the landing/portal page for this recreated site.

FIDELITY RULES (highest priority — violation = failure):
- Copy the EXACT layout, structure, and visual appearance from the source data.
- Use ONLY text, headings, links and sections present in the source data below — character for character.
- Do NOT invent statistics, feature counts, testimonials or product names.
- Do NOT add extra decoration, animations, gradients or visual polish not present in the original.
- If source data is sparse, build a clean minimal page from just what is there.

REQUIREMENTS:
{requirements_block}

PAGES:
{pages_list}

NAV: {_format_nav(nav_items)}
COLORS: {colors}
FONTS: {fonts}

{_output_rules(stack)}"""
