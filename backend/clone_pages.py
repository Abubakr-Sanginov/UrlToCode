"""Generating one page of a clone, outside the clone websocket.

The first generation runs inside the websocket handler, which has the live
crawl in hand. Everything else that has to produce a page again — the user
asking for one page to be redone, the visual check deciding a page is wrong
enough to repair — works from a stored run instead.

Both paths call `generate_page` here, so a regenerated page is built by
exactly the same rules as the original: same prompt, same stack contract, same
acceptance test for "is this a usable file". A repair that used different rules
would be fixing a page to a different specification than the one it was
generated to.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from clone_runs import CloneRun
import clone_cache
import clone_cost
from crawler.crawler import CrawlPage, CrawlResult
from crawler.media_store import rewrite_media_urls
from llm_http import Completion, Image, LlmConfig, complete
from prompts.framework_stacks import (
    build_head,
    build_route_map,
    clean_component_output,
    is_framework_stack,
    is_usable_component,
    route_for_path,
    wrap_component_preview,
)
from prompts.url_to_code_prompts import PAGE_PROMPT_VERSION, build_link_map, build_page_prompt

# Per-page structural markup budget before the page is handed to the model.
# The compactor typically shrinks real pages 10-150x, so this holds a whole
# page skeleton rather than an arbitrary head-slice of raw markup.
RAW_HTML_LIMIT = 200_000

# One model call has to emit the whole document...
GENERATE_USER_TURN = (
    "Output the complete file now. Start with <!DOCTYPE html> and end with "
    "</html>. No explanation, no plan, no markdown fences."
)

# ...and framework stacks answer with a page component, not a document.
COMPONENT_USER_TURN = (
    "Output the complete .tsx file now, ending with the closing brace of the "
    "default export. No explanation, no plan, no markdown fences."
)


def html_document(text: str) -> str:
    """Return the HTML document inside `text`, or "" if there is none."""
    lowered = text.lower()
    start = lowered.find("<!doctype html")
    if start == -1:
        start = lowered.find("<html")
    if start == -1:
        return ""
    end = lowered.rfind("</html>")
    return text[start : end + len("</html>")] if end != -1 else text[start:]


def clean_llm_output(text: str) -> str:
    """Pull the generated file out of whatever the model wrapped it in.

    Models narrate ("For the masthead structure, I'll recreate with divs:")
    and fence their code, sometimes in several blocks. Taking the first fence
    shipped the narration or one fragment as the clone, so the document
    itself is preferred and fenced blocks are only a fallback.
    """
    text = text.strip()

    document = html_document(text)
    if document:
        return document.strip()

    blocks = re.findall(
        r"```(?:[a-zA-Z]*)\s*\n(.*?)```",
        text,
        re.DOTALL,
    )
    if blocks:
        # Several fences means the answer was split into fragments; the
        # largest one is the file, the rest are snippets being discussed.
        return max(blocks, key=len).strip()

    # An unterminated fence: the answer was cut off mid-block.
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    return text


def is_usable_html(text: str) -> bool:
    """True when `text` is a complete HTML document rather than commentary."""
    if not text:
        return False
    lowered = text.lower()
    if "<!doctype html" not in lowered and "<html" not in lowered:
        return False
    return "</html>" in lowered


def page_data_of(page: CrawlPage) -> Dict[str, Any]:
    """The shape the prompt builders expect one crawled page in."""
    return {
        "url": page.url,
        "path": page.path,
        "title": page.title,
        "html": page.html[:RAW_HTML_LIMIT],
        "screenshot": page.screenshot,
        "forms": page.forms,
        "navigation": page.navigation,
        "images": page.images[:20],
        "videos": page.videos,
        "design": page.design,
        "meta": page.meta,
        "depth": page.depth,
        # What the page looked like at each width the crawl captured, and what
        # that says about how it reflows. Both are absent for a plain crawl,
        # and the prompt says so rather than inventing a mobile layout.
        "viewport_screenshots": page.viewport_screenshots,
        "layout_notes": page.layout_notes,
        # States the page reveals when its own controls are used. Empty unless
        # the crawl was asked to capture them.
        "states": page.states,
        "embedded": page.embedded,
    }


def link_map_for(crawl: CrawlResult, stack: str) -> Dict[str, str]:
    """Where each crawled page lives inside the generated project.

    Pages link to each other through their local files (or routes, for a
    framework project), so the saved project navigates like the original
    instead of leaving for the live site.
    """
    paths: List[str] = [page.path for page in crawl.pages]
    if is_framework_stack(stack):
        return build_route_map(paths)
    return build_link_map(paths)


def site_head_for(crawl: CrawlResult) -> Dict[str, Any]:
    """Fonts, favicon and base colors: site-wide, so the home page decides."""
    home = next((page for page in crawl.pages if page.path == "/"), crawl.pages[0])
    return build_head(home.meta, home.design)


def media_mapper_for(
    crawl: CrawlResult, media_base_url: str
) -> Callable[[str], str]:
    """Rewrite captured media URLs to the copies this backend serves."""
    all_media: Dict[str, str] = {}
    for page in crawl.pages:
        all_media.update(page.media)
    if not all_media:
        return lambda code: code
    return lambda code: rewrite_media_urls(code, all_media, media_base_url)


def page_prompt_for(
    page_data: Dict[str, Any],
    stack: str,
    generate_database: bool,
    link_map: Dict[str, str],
) -> str:
    messages = build_page_prompt(page_data, stack, generate_database, link_map)
    return messages[0]["content"] if messages else ""


def user_turn_for(stack: str) -> str:
    return COMPONENT_USER_TURN if is_framework_stack(stack) else GENERATE_USER_TURN


def finish_page(
    text: str,
    page_data: Dict[str, Any],
    stack: str,
    site_head: Dict[str, Any],
    localize: Callable[[str], str],
) -> str:
    """Turn a model answer into the stored page, or "" when unusable."""
    if is_framework_stack(stack):
        component = clean_component_output(text)
        if not is_usable_component(component):
            return ""
        # Captured head images (favicon) are localized with the page.
        return localize(
            wrap_component_preview(
                component,
                stack,
                route_for_path(page_data.get("path", "/")),
                page_data.get("title", "Page"),
                site_head,
            )
        )
    cleaned = clean_llm_output(text)
    return localize(cleaned) if is_usable_html(cleaned) else ""


async def generate_page(
    run: CloneRun,
    path: str,
    cfg: LlmConfig,
    media_base_url: str,
    instruction: str = "",
    existing_code: str = "",
    budget: Optional[clone_cost.Budget] = None,
    images: Optional[List[Image]] = None,
) -> tuple[str, str]:
    """Generate one page of `run` from its stored crawl.

    Returns `(code, error)`; `code` is empty when the model did not answer
    with a usable file, and `error` says why.

    `instruction` and `existing_code` turn this into a repair: the prompt is
    then the page's normal prompt plus what to change and the code as it
    stands, which is what the visual check feeds back after a diff. A repair
    is never cached - its whole point is that the cached page was wrong.

    `images` are the pictures a repair is judged from: the original page and
    the page as it currently renders. They are only sent to a model that can
    see; a model that cannot is given the description instead, because a
    model handed an image it cannot read answers from the words around it
    and gives a confident wrong fix.

    Otherwise the page comes from the cache when one was kept for this site,
    this stack, this model and this version of the prompt. That is the
    difference between retrying a clone and paying for it again.

    A `budget` is checked before the call and updated after it, so a run
    under a ceiling stops with an explanation rather than one page past it.
    """
    if run.crawl is None:
        return "", "This run has no stored crawl to generate from."

    page = next((p for p in run.crawl.pages if p.path == path), None)
    if page is None:
        return "", f"No crawled page matches {path!r}."

    if not cfg.is_usable:
        return "", "No model provider configured."

    stack = run.stack
    generate_database = bool(run.params.get("generateDatabase"))

    cache_key = None
    if not instruction:
        cache_key = clone_cache.key_for(
            run.base_url,
            path,
            stack,
            cfg.model or "",
            PAGE_PROMPT_VERSION,
            generate_database,
        )
        cached = clone_cache.get(cache_key)
        if cached is not None:
            return cached, ""

    page_data = page_data_of(page)
    link_map = link_map_for(run.crawl, stack)
    site_head = site_head_for(run.crawl)
    localize = media_mapper_for(run.crawl, media_base_url)
    prompt = page_prompt_for(page_data, stack, generate_database, link_map)

    if instruction:
        # A repair starts from what the page looks like now, not from the
        # original markup, so the model edits rather than re-invents.
        if existing_code:
            prompt += (
                "\n\nCURRENT OUTPUT OF THIS PAGE:\n" + existing_code[:RAW_HTML_LIMIT]
            )
        prompt += (
            "\n\nWHAT TO CHANGE:\n" + instruction
            + "\n\nOutput the corrected complete file only."
        )

    # Checked before the call, not after: a ceiling that is enforced once the
    # bill has already been spent is not a ceiling.
    if budget is not None:
        try:
            budget.check(cfg.model or "")
        except clone_cost.CeilingExceeded as exc:
            return "", str(exc)

    try:
        result: Completion = await complete(
            cfg, prompt, user_turn_for(stack), images=images
        )
    except Exception as exc:  # provider refused, timed out, or the call failed
        return "", str(exc)

    if budget is not None:
        clone_cost.record_from_usage(budget, cfg.model or "", result.usage, prompt, result.text or "")

    if not result.text:
        return "", result.empty_reason or "the model returned nothing"

    code = finish_page(result.text, page_data, stack, site_head, localize)
    if not code:
        return "", "the model did not return a usable page"
    if cache_key is not None:
        clone_cache.put(cache_key, code)
    return code, ""
