import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal
from urllib.parse import urlparse
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    IS_DEBUG_ENABLED,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENROUTER_API_KEY,
    REPLICATE_API_KEY,
)
from llm_http import (
    Completion,
    LlmConfig,
    ProviderError,
    REQUEST_TIMEOUT_SECONDS,
    complete,
)
from crawler.crawler import SiteCrawler, CrawlResult, CrawlPage
from prompts.url_to_code_prompts import (
    build_link_map,
    build_page_prompt,
    build_database_schema_prompt,
    build_project_structure_prompt,
    build_single_file_prompt,
)
from ws.constants import APP_ERROR_WEB_SOCKET_CODE

router = APIRouter()

# Upper bound on raw markup carried per page before prompt-time compaction.
# Compaction needs enough of the document to see structure, but pages beyond
# this size add no signal and cost memory.
RAW_HTML_LIMIT = 200_000

# One model call has to emit the whole document, so a site only fits in a
# single file while it stays small. Past these limits the answer runs into the
# output token ceiling and comes back truncated, which is worse than a project
# of per-page files.
# Spelling the contract out in the user turn as well: models that ignore it
# in the system prompt answer with a plan instead of the file.
GENERATE_USER_TURN = (
    "Output the complete file now. Start with <!DOCTYPE html> and end with "
    "</html>. No explanation, no plan, no markdown fences."
)

SINGLE_FILE_MAX_PAGES = 3
SINGLE_FILE_MAX_PROMPT_CHARS = 12_000


def _single_file_too_large(crawl_result: CrawlResult, prompt: str) -> str:
    """Say why this site will not fit in one file, or "" if it will."""
    page_count = len(crawl_result.pages)
    if page_count > SINGLE_FILE_MAX_PAGES:
        return f"{page_count} pages crawled"
    if len(prompt) > SINGLE_FILE_MAX_PROMPT_CHARS:
        return f"{len(prompt):,} characters of page structure"
    return ""


def _html_document(text: str) -> str:
    """Return the HTML document inside `text`, or "" if there is none."""
    lowered = text.lower()
    start = lowered.find("<!doctype html")
    if start == -1:
        start = lowered.find("<html")
    if start == -1:
        return ""
    end = lowered.rfind("</html>")
    return text[start : end + len("</html>")] if end != -1 else text[start:]


def _clean_llm_output(text: str) -> str:
    """Pull the generated file out of whatever the model wrapped it in.

    Models narrate ("For the masthead structure, I'll recreate with divs:")
    and fence their code, sometimes in several blocks. Taking the first fence
    shipped the narration or one fragment as the clone, so the document
    itself is preferred and fenced blocks are only a fallback.
    """
    text = text.strip()

    document = _html_document(text)
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


def _is_usable_html(text: str) -> bool:
    """True when `text` is a complete HTML document rather than commentary."""
    if not text:
        return False
    lowered = text.lower()
    if "<!doctype html" not in lowered and "<html" not in lowered:
        return False
    return "</html>" in lowered


@dataclass
class UrlToCodeParams:
    url: str
    stack: str = "html-tailwind"
    max_pages: int = 30
    max_depth: int = 4
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    replicate_api_key: str | None = None
    openai_base_url: str | None = None
    generate_database: bool = True
    generate_auth: bool = True
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    # Direct-provider models. The UI may not offer a picker for these, in
    # which case the configured default is used instead of a hard-coded id.
    anthropic_model: str | None = None
    openai_model: str | None = None
    gemini_model: str | None = None
    # Custom OpenAI-compatible provider (Ollama, vLLM, etc.)
    custom_provider_base_url: str | None = None
    custom_provider_api_key: str | None = None
    custom_provider_model: str | None = None
    # Pack every page into one HTML file instead of generating per-page files.
    single_file: bool = False


UrlMessageType = Literal[
    "status",
    "progress",
    "crawlComplete",
    "pageStart",
    "pageComplete",
    "setCode",
    "partialCode",
    "error",
    "variantComplete",
    "variantModels",
    "thinking",
    "assistant",
    "toolStart",
    "toolResult",
]


async def send_ws_message(
    websocket: WebSocket,
    type: UrlMessageType,
    value: str | None = None,
    data: Dict[str, Any] | None = None,
    is_closed: bool = False,
) -> bool:
    if is_closed:
        return True
    try:
        payload: Dict[str, Any] = {"type": type}
        if value is not None:
            payload["value"] = value
        if data is not None:
            payload["data"] = data
        await websocket.send_json(payload)
        return False
    except (ConnectionClosedOK, ConnectionClosedError, RuntimeError, WebSocketDisconnect):
        return True


def _get_api_key(params_key: str | None, env_key: str | None) -> str | None:
    return params_key or env_key


def _llm_config_for(params: UrlToCodeParams) -> LlmConfig:
    """Pick the provider to use, most specific configuration first.

    Every provider takes its model from the request (or the environment), so
    a model chosen in Settings is honoured instead of being overridden by a
    hard-coded id.
    """
    if params.custom_provider_base_url:
        return LlmConfig(
            provider="custom",
            model=params.custom_provider_model or "",
            api_key=params.custom_provider_api_key or "no-key",
            base_url=params.custom_provider_base_url,
        )

    openrouter_key = _get_api_key(params.openrouter_api_key, OPENROUTER_API_KEY)
    if openrouter_key:
        return LlmConfig(
            provider="openrouter",
            model=params.openrouter_model or "",
            api_key=openrouter_key,
        )

    anthropic_key = _get_api_key(params.anthropic_api_key, ANTHROPIC_API_KEY)
    if anthropic_key:
        return LlmConfig(
            provider="anthropic",
            model=params.anthropic_model or ANTHROPIC_MODEL or "",
            api_key=anthropic_key,
        )

    openai_key = _get_api_key(params.openai_api_key, OPENAI_API_KEY)
    if openai_key:
        return LlmConfig(
            provider="openai",
            model=params.openai_model or OPENAI_MODEL or "",
            api_key=openai_key,
            base_url=params.openai_base_url or OPENAI_BASE_URL or "",
        )

    gemini_key = _get_api_key(params.gemini_api_key, GEMINI_API_KEY)
    if gemini_key:
        return LlmConfig(
            provider="gemini",
            model=params.gemini_model or GEMINI_MODEL or "",
            api_key=gemini_key,
        )

    return LlmConfig()


async def _run_agent_for_page(
    page_data: Dict[str, Any],
    params: UrlToCodeParams,
    websocket: WebSocket,
    page_index: int,
    total_pages: int,
    link_map: Dict[str, str] | None = None,
) -> Completion:
    prompt_messages = build_page_prompt(
        page_data, params.stack, params.generate_database, link_map
    )
    system_msg = prompt_messages[0]["content"] if prompt_messages else ""

    try:
        return await complete(_llm_config_for(params), system_msg, GENERATE_USER_TURN)
    except ProviderError:
        # Per-page failures are reported individually, but a provider-level
        # refusal (retired model, bad key, rate limit) will hit every page, so
        # let it abort the run with the real reason.
        raise
    except Exception as e:
        print(f"Error generating page {page_data.get('path', '?')}: {e}")
        return Completion(empty_reason=str(e))


# Pages are generated concurrently; more than a handful of parallel calls
# gets free tiers rate limited, which costs more time than it saves.
PAGE_CONCURRENCY = 3

MAX_PAGES = 30
MAX_DEPTH = 6
DEFAULT_PAGES = 10
DEFAULT_DEPTH = 2
VALID_STACKS = frozenset(
    {
        "html_tailwind",
        "html_css",
        "react_tailwind",
        "bootstrap",
        "vue_tailwind",
        "ionic_tailwind",
    }
)


def _clamp_int(raw: Any, default: int, low: int, high: int) -> int:
    """Coerce an untrusted websocket value into `low..high`.

    A non-numeric or missing value falls back to `default` rather than raising
    and killing the connection before the client sees an error message.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _normalize_stack(raw: Any) -> str:
    stack = str(raw or "").replace("-", "_")
    return stack if stack in VALID_STACKS else "html_tailwind"


@router.websocket("/url-to-code")
async def url_to_code_ws(websocket: WebSocket):
    await websocket.accept()
    is_closed = False

    try:
        raw_params = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    params = UrlToCodeParams(
        url=str(raw_params.get("url") or "").strip(),
        stack=_normalize_stack(raw_params.get("stack")),
        max_pages=_clamp_int(raw_params.get("maxPages"), DEFAULT_PAGES, 1, MAX_PAGES),
        max_depth=_clamp_int(raw_params.get("maxDepth"), DEFAULT_DEPTH, 1, MAX_DEPTH),
        openai_api_key=raw_params.get("openAiApiKey"),
        anthropic_api_key=raw_params.get("anthropicApiKey"),
        gemini_api_key=raw_params.get("geminiApiKey"),
        replicate_api_key=raw_params.get("replicateApiKey"),
        openai_base_url=raw_params.get("openAiBaseURL"),
        generate_database=bool(raw_params.get("generateDatabase", False)),
        generate_auth=bool(raw_params.get("generateAuth", False)),
        openrouter_api_key=raw_params.get("openRouterApiKey") or OPENROUTER_API_KEY,
        openrouter_model=raw_params.get("openRouterModel"),
        anthropic_model=raw_params.get("anthropicModel"),
        openai_model=raw_params.get("openAiModel"),
        gemini_model=raw_params.get("geminiModel"),
        # Custom OpenAI-compatible provider
        custom_provider_base_url=raw_params.get("customProviderBaseUrl"),
        custom_provider_api_key=raw_params.get("customProviderApiKey"),
        custom_provider_model=raw_params.get("customProviderModel"),
        single_file=bool(raw_params.get("singleFile", False)),
    )

    if not params.url:
        is_closed = await send_ws_message(websocket, "error", "URL is required", is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # Fail fast with an actionable message instead of crawling first and only
    # then discovering there is no model to generate with.
    llm_cfg = _llm_config_for(params)
    if not llm_cfg.is_usable:
        is_closed = await send_ws_message(
            websocket,
            "error",
            "No model provider configured. Add an OpenRouter, Anthropic, OpenAI, "
            "Gemini, or custom provider key in Settings.",
            is_closed=is_closed,
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # The same model that generates the code also guides the crawl: it looks
    # at each page's interactive elements, decides what to click/type (e.g.
    # "this is a search box, query X"), and we capture the result.
    llm_config: Dict[str, Any] = {
        "provider": llm_cfg.provider,
        "model": llm_cfg.model,
        "api_key": llm_cfg.api_key,
        "base_url": llm_cfg.base_url,
    }

    async def crawl_progress(status: str, current: int, total: int) -> None:
        nonlocal is_closed
        is_closed = await send_ws_message(
            websocket,
            "progress",
            status,
            data={"current": current, "total": total, "phase": "crawling"},
            is_closed=is_closed,
        )

    crawler = SiteCrawler(
        max_pages=params.max_pages,
        max_depth=params.max_depth,
        llm_config=llm_config,
    )
    crawler.set_progress_callback(crawl_progress)

    is_closed = await send_ws_message(websocket, "status", "Starting website crawl...", is_closed=is_closed)

    try:
        print(f"[URL2CODE] Starting crawl of {params.url}")
        crawl_result = await crawler.crawl(params.url)
        print(f"[URL2CODE] Crawl complete: {len(crawl_result.pages)} pages, error={crawl_result.error}")
    except Exception as e:
        print(f"[URL2CODE] Crawl exception: {e}")
        is_closed = await send_ws_message(websocket, "error", f"Crawl failed: {str(e)}", is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    if crawl_result.error:
        is_closed = await send_ws_message(websocket, "error", crawl_result.error, is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # A site behind a bot check hands back "Just a moment..." for every page.
    # Generating from that produces a blank clone, so say what happened.
    if crawl_result.pages and all(page.blocked for page in crawl_result.pages):
        is_closed = await send_ws_message(
            websocket,
            "error",
            "The site is behind a bot check (Cloudflare) and served a "
            "verification page instead of its content. Cloning it is not "
            "possible from here — try a different URL.",
            is_closed=is_closed,
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # Some pages got through the bot check and some did not: clone only the
    # real ones instead of reproducing "Just a moment..." as a page.
    real_pages = [page for page in crawl_result.pages if not page.blocked]
    if real_pages and len(real_pages) < len(crawl_result.pages):
        skipped = len(crawl_result.pages) - len(real_pages)
        print(f"[URL2CODE] Skipping {skipped} bot-check pages")
        crawl_result.pages = real_pages

    if not crawl_result.pages:
        print(f"[URL2CODE] No pages crawled, generating from URL alone: {crawl_result.base_url}")
        netloc = urlparse(crawl_result.base_url).netloc
        site_name = netloc.replace("www.", "").split(".")[0].capitalize()
        synthetic_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{site_name}</title>
  <meta name="description" content="{site_name} - A modern website">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>body {{ font-family: 'Inter', sans-serif; margin: 0; padding: 0; }}</style>
</head>
<body>
  <nav><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav>
  <main>
    <h1>Welcome to {site_name}</h1>
    <p>Discover our products and services. We offer the best solutions for your needs.</p>
    <section>
      <h2>Our Features</h2>
      <div>Feature 1: Quality products</div>
      <div>Feature 2: Fast delivery</div>
      <div>Feature 3: Great support</div>
    </section>
  </main>
  <footer><p>© 2024 {site_name}. All rights reserved.</p></footer>
</body>
</html>"""
        synthetic_page = CrawlPage(
            url=crawl_result.base_url,
            path="/",
            title=site_name,
            html=synthetic_html,
            links=[
                crawl_result.base_url + "/about",
                crawl_result.base_url + "/contact",
                crawl_result.base_url + "/products",
            ],
            forms=[],
            navigation=[
                {"text": "Home", "href": crawl_result.base_url},
                {"text": "About", "href": crawl_result.base_url + "/about"},
                {"text": "Contact", "href": crawl_result.base_url + "/contact"},
            ],
            images=[],
            depth=0,
        )
        crawl_result.pages = [synthetic_page]

    pages_data: List[Dict[str, Any]] = []
    for page in crawl_result.pages:
        pages_data.append(
            {
                "url": page.url,
                "path": page.path,
                "title": page.title,
                "html": page.html[:RAW_HTML_LIMIT],
                "screenshot": page.screenshot,
                "forms": page.forms,
                "navigation": page.navigation,
                "images": page.images[:10],
                "depth": page.depth,
            }
        )

    # Pages link to each other through their local files, so the saved
    # project navigates like the original instead of leaving for the live site.
    link_map = build_link_map(p["path"] for p in pages_data)

    crawl_payload: Dict[str, Any] = {
        "pagesFound": len(crawl_result.pages),
        "siteStructure": crawl_result.site_structure,
        "designTokens": crawl_result.design_tokens,
        "pages": [
            {"path": p["path"], "title": p["title"], "depth": p["depth"]}
            for p in pages_data
        ],
    }
    is_closed = await send_ws_message(websocket, "crawlComplete", json.dumps(crawl_payload), data=crawl_payload, is_closed=is_closed)

    # A single file is one model call for the whole site, so a large crawl is
    # generated per page instead - a truncated document helps nobody.
    single_prompt = ""
    use_single_file = params.single_file
    if params.single_file:
        single_prompt = build_single_file_prompt(crawl_result, params.stack)
        too_large = _single_file_too_large(crawl_result, single_prompt)
        if too_large:
            use_single_file = False
            print(f"[URL2CODE] Single file not viable ({too_large}); generating per page")
            is_closed = await send_ws_message(
                websocket,
                "status",
                f"Too large for one file ({too_large}) - generating one file "
                "per page instead.",
                is_closed=is_closed,
            )

    # A provider-level refusal applies to every request in the run, so it is
    # reported verbatim and aborts instead of being retried per page.
    try:
        all_code: Dict[str, str]

        if use_single_file:
            # One LLM call packs every page into a single HTML document.
            is_closed = await send_ws_message(
                websocket, "status", "Generating single-file clone...", is_closed=is_closed
            )
            print(f"[URL2CODE] Single-file prompt length: {len(single_prompt)} chars")

            commentary = ""
            last_result = Completion()

            async def attempt_single_file(prompt: str, prefix: str) -> str | None:
                """Generate once and accept only a complete HTML document.

                Models answer with a plan ("For the masthead structure, I'll
                recreate with divs:") or with the document cut off mid-way;
                both used to be delivered as the finished clone.
                """
                nonlocal commentary, last_result
                last_result = await _generate_with_llm(
                    prompt, params, websocket, is_closed, event_prefix=prefix
                )
                if not last_result.text:
                    return None
                cleaned = _clean_llm_output(last_result.text)
                if _is_usable_html(cleaned):
                    return cleaned
                commentary = " ".join(cleaned.split())[:160]
                print(f"[URL2CODE] {prefix}: not a complete HTML document: {commentary}")
                return None

            single_html = await attempt_single_file(single_prompt, "single-file")

            # Free models sometimes return an empty body or a plan on the
            # first call; one retry saves the run instead of a hard error.
            if not single_html:
                print("[URL2CODE] Unusable single-file response, retrying...")
                is_closed = await send_ws_message(
                    websocket, "status", "Retrying generation...", is_closed=is_closed
                )
                single_html = await attempt_single_file(
                    single_prompt, "single-file-retry"
                )

            # A model that ran out of output budget will do so again on an
            # identical prompt; asking for fewer, smaller pages is the only
            # retry with a different outcome.
            if not single_html and last_result.truncated:
                print("[URL2CODE] Output limit hit, retrying with a smaller prompt")
                is_closed = await send_ws_message(
                    websocket,
                    "status",
                    "Model ran out of output budget - retrying with fewer pages...",
                    is_closed=is_closed,
                )
                single_html = await attempt_single_file(
                    build_single_file_prompt(
                        crawl_result, params.stack, per_page_budget=2000, max_pages=2
                    ),
                    "single-file-compact",
                )

            if not single_html:
                if commentary:
                    detail = (
                        " - it answered with commentary instead of a complete "
                        f'HTML file ("{commentary}")'
                    )
                elif last_result.empty_reason:
                    detail = f" ({last_result.empty_reason})"
                else:
                    detail = ""
                is_closed = await send_ws_message(
                    websocket,
                    "error",
                    f"The model did not return a usable single-file clone{detail}. "
                    "Retry, or pick a different model in Settings.",
                    is_closed=is_closed,
                )
                if not is_closed:
                    await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
                return

            all_code = {"/": single_html}
            total_pages_to_gen = 1
            print(f"[URL2CODE] Single-file clone: {len(single_html)} chars")

        else:
            is_closed = await send_ws_message(websocket, "status", "Generating project structure...", is_closed=is_closed)

            project_structure_prompt = build_project_structure_prompt(
                crawl_result, params.stack, params.generate_database, params.generate_auth
            )
            print(f"[URL2CODE] Project structure prompt length: {len(project_structure_prompt)} chars")

            structure_result = await _generate_with_llm(
                project_structure_prompt,
                params,
                websocket,
                is_closed,
                event_prefix="structure",
            )
            if structure_result.text and not _is_usable_html(
                _clean_llm_output(structure_result.text)
            ):
                # A plan instead of a page. Ask once more before giving up.
                print("[URL2CODE] Structure answer was not HTML, retrying...")
                is_closed = await send_ws_message(
                    websocket, "status", "Retrying generation...", is_closed=is_closed
                )
                structure_result = await _generate_with_llm(
                    project_structure_prompt,
                    params,
                    websocket,
                    is_closed,
                    event_prefix="structure-retry",
                )

            # The portal is a generated overview, not part of the copy: losing
            # it must not throw away the pages, which are the actual clone.
            portal = (
                _clean_llm_output(structure_result.text) if structure_result.text else ""
            )
            all_code = {}
            if _is_usable_html(portal):
                all_code["project-structure"] = portal
            else:
                print(
                    "[URL2CODE] No usable landing page "
                    f"({structure_result.empty_reason or 'not HTML'}); continuing with pages"
                )

            if params.generate_database:
                is_closed = await send_ws_message(
                    websocket, "status", "Generating database schema...", is_closed=is_closed
                )
                db_prompt = build_database_schema_prompt(crawl_result, params.stack)
                db_schema = await _generate_with_llm(
                    db_prompt, params, websocket, is_closed, event_prefix="database"
                )
                if db_schema.text:
                    all_code["database-schema"] = _clean_llm_output(db_schema.text)

            total_pages_to_gen = len(pages_data)
            is_closed = await send_ws_message(
                websocket,
                "status",
                f"Generating {total_pages_to_gen} pages "
                f"({PAGE_CONCURRENCY} at a time)...",
                is_closed=is_closed,
            )

            # Pages are independent calls, so waiting for each one in turn
            # made a ten-page site a twenty-minute run. The limit keeps free
            # tiers from answering every request with a rate-limit error.
            budget = asyncio.Semaphore(PAGE_CONCURRENCY)

            async def generate_page(index: int, page_data: Dict[str, Any]):
                async with budget:
                    result = await _run_agent_for_page(
                        page_data,
                        params,
                        websocket,
                        index,
                        total_pages_to_gen,
                        link_map=link_map,
                    )
                return index, page_data.get("path", "/"), result

            tasks = [
                asyncio.create_task(generate_page(i, page_data))
                for i, page_data in enumerate(pages_data)
            ]

            try:
                # Messages are sent from here only: one websocket cannot be
                # written from several tasks at once.
                for finished in asyncio.as_completed(tasks):
                    index, page_path, result = await finished

                    cleaned_page = _clean_llm_output(result.text) if result.text else ""
                    if cleaned_page and not _is_usable_html(cleaned_page):
                        # Commentary or a half-written document; report the page
                        # as failed rather than saving a file that will not render.
                        print(f"[URL2CODE] {page_path}: answer was not a complete HTML file")
                        cleaned_page = ""

                    if cleaned_page:
                        all_code[page_path] = cleaned_page
                        is_closed = await send_ws_message(
                            websocket,
                            "pageComplete",
                            f"Completed: {page_path}",
                            data={
                                "pageIndex": index,
                                "totalPages": total_pages_to_gen,
                                "path": page_path,
                                "codeLength": len(cleaned_page),
                            },
                            is_closed=is_closed,
                        )
                        # Hand over what exists so far: a connection that drops
                        # on the last page no longer costs the whole run.
                        is_closed = await send_ws_message(
                            websocket,
                            "partialCode",
                            f"{len(all_code)} files ready",
                            data={"code": dict(all_code)},
                            is_closed=is_closed,
                        )
                    else:
                        is_closed = await send_ws_message(
                            websocket,
                            "pageComplete",
                            f"Failed: {page_path}",
                            data={
                                "pageIndex": index,
                                "totalPages": total_pages_to_gen,
                                "path": page_path,
                                "error": True,
                                "reason": result.empty_reason,
                            },
                            is_closed=is_closed,
                        )
            finally:
                # A provider refusal aborts the run; nothing should outlive it.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    except ProviderError as exc:
        is_closed = await send_ws_message(
            websocket, "error", str(exc), is_closed=is_closed
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    generated_pages = [
        key
        for key, value in all_code.items()
        if key not in ("project-structure", "database-schema") and value
    ]
    print(
        f"[URL2CODE] Final: project-structure="
        f"{len(all_code.get('project-structure', ''))} chars, "
        f"pages={len(generated_pages)}"
    )

    if not all_code.get("project-structure") and generated_pages:
        fallback = generated_pages[0]
        print(f"[URL2CODE] Using first page '{fallback}' as main code")
        all_code["project-structure"] = all_code[fallback]

    # Every page failed. Say so instead of shipping an empty payload the
    # frontend would have to guess about.
    if not any(all_code.values()):
        is_closed = await send_ws_message(
            websocket,
            "error",
            "Every page failed to generate. The model may be rate limited or "
            "returning empty responses — retry or pick a different model.",
            is_closed=is_closed,
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    if not is_closed:
        final_payload: Dict[str, Any] = {
            "code": all_code,
            "pagesGenerated": len([v for v in all_code.values() if v]),
            "totalPages": total_pages_to_gen,
            "baseUrl": crawl_result.base_url,
        }
        is_closed = await send_ws_message(
            websocket,
            "setCode",
            json.dumps(final_payload),
            data=final_payload,
            is_closed=is_closed,
        )
        is_closed = await send_ws_message(
            websocket,
            "variantComplete",
            "Full site generation complete",
            is_closed=is_closed,
        )

    if not is_closed:
        await websocket.close()


async def _generate_with_llm(
    prompt: str,
    params: UrlToCodeParams,
    websocket: WebSocket,
    is_closed: bool,
    event_prefix: str = "gen",
) -> Completion:
    """One model call, with the provider's own failure reason preserved."""
    cfg = _llm_config_for(params)
    print(
        f"[URL2CODE] LLM {event_prefix}: provider={cfg.provider}, "
        f"model={cfg.model or 'default'}, key={'set' if cfg.api_key else 'missing'}"
    )

    try:
        # The HTTP client enforces the same deadline; this only guards against
        # a client that never returns at all.
        result = await asyncio.wait_for(
            complete(cfg, prompt, GENERATE_USER_TURN),
            timeout=REQUEST_TIMEOUT_SECONDS + 30,
        )
    except asyncio.TimeoutError:
        print(f"Timeout in {event_prefix} generation after {REQUEST_TIMEOUT_SECONDS}s")
        return Completion(empty_reason="the model did not answer in time")
    except ProviderError:
        # The provider said why it refused; let the caller report that verbatim
        # rather than collapsing it into a generic failure.
        raise
    except Exception as e:
        print(f"Error in {event_prefix} generation: {e}")
        return Completion(empty_reason=str(e))

    if not result:
        print(f"[LLM] Empty response for {event_prefix}: {result.empty_reason}")
    return result
