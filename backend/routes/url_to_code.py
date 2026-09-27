import asyncio
import json
import httpx
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, cast
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from config import (
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
    IS_DEBUG_ENABLED,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    REPLICATE_API_KEY,
)
from crawler.crawler import SiteCrawler, CrawlResult, CrawlPage
from prompts.url_to_code_prompts import (
    build_page_prompt,
    build_database_schema_prompt,
    build_project_structure_prompt,
)
from ws.constants import APP_ERROR_WEB_SOCKET_CODE

router = APIRouter()


def _clean_llm_output(text: str) -> str:
    """Strip markdown code fences and extract raw code from LLM output."""
    import re

    text = text.strip()

    code_block_match = re.search(
        r"```(?:html|htm|xml|javascript|js|css)?\s*\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if code_block_match:
        return code_block_match.group(1).strip()

    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    return text


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


UrlMessageType = Literal[
    "status",
    "progress",
    "crawlComplete",
    "pageStart",
    "pageComplete",
    "setCode",
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


def _get_model_for_provider(params: UrlToCodeParams) -> tuple[str, str | None, str | None]:
    """Returns (provider, model_id, api_key) for the best available provider."""
    # OpenRouter has priority if configured
    if params.openrouter_api_key:
        model = params.openrouter_model or "meta-llama/llama-3.3-70b-instruct:free"
        return ("openrouter", model, params.openrouter_api_key)
    
    # Fall back to direct providers
    if params.anthropic_api_key:
        return ("anthropic", None, params.anthropic_api_key)
    if params.openai_api_key:
        return ("openai", None, params.openai_api_key)
    if params.gemini_api_key:
        return ("gemini", None, params.gemini_api_key)
    
    return ("none", None, None)


async def _run_agent_for_page(
    page_data: Dict[str, Any],
    params: UrlToCodeParams,
    websocket: WebSocket,
    page_index: int,
    total_pages: int,
) -> str | None:
    provider, model_id, api_key = _get_model_for_provider(params)

    prompt_messages = build_page_prompt(page_data, params.stack, params.generate_database)
    system_msg = prompt_messages[0]["content"] if prompt_messages else ""
    user_msg = "Generate the code now."

    try:
        if provider == "openrouter":
            openrouter_key = params.openrouter_api_key or OPENROUTER_API_KEY
            if not openrouter_key:
                return None
            result = await _call_openrouter(
                openrouter_key, model_id or "meta-llama/llama-3.3-70b-instruct:free",
                [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]
            )
            return result
        elif provider == "anthropic":
            anthropic_key = _get_api_key(params.anthropic_api_key, ANTHROPIC_API_KEY)
            if not anthropic_key:
                return None
            result = await _call_anthropic(
                anthropic_key,
                [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]
            )
            return result
        elif provider == "openai":
            openai_key = _get_api_key(params.openai_api_key, OPENAI_API_KEY)
            if not openai_key:
                return None
            result = await _call_openai(
                openai_key, params.openai_base_url,
                [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]
            )
            return result
        elif provider == "gemini":
            gemini_key = _get_api_key(params.gemini_api_key, GEMINI_API_KEY)
            if not gemini_key:
                return None
            result = await _call_gemini(
                gemini_key,
                [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]
            )
            return result
        else:
            return None
    except Exception as e:
        print(f"Error generating page {page_data.get('path', '?')}: {e}")
        return None


@router.websocket("/url-to-code")
async def url_to_code_ws(websocket: WebSocket):
    await websocket.accept()
    is_closed = False

    try:
        raw_params = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    params = UrlToCodeParams(
        url=raw_params.get("url", ""),
        stack=raw_params.get("stack", "html-tailwind"),
        max_pages=min(int(raw_params.get("maxPages", 10)), 30),
        max_depth=min(int(raw_params.get("maxDepth", 4)), 6),
        openai_api_key=raw_params.get("openAiApiKey"),
        anthropic_api_key=raw_params.get("anthropicApiKey"),
        gemini_api_key=raw_params.get("geminiApiKey"),
        replicate_api_key=raw_params.get("replicateApiKey"),
        openai_base_url=raw_params.get("openAiBaseURL"),
        generate_database=raw_params.get("generateDatabase", True),
        generate_auth=raw_params.get("generateAuth", True),
        openrouter_api_key=raw_params.get("openRouterApiKey") or OPENROUTER_API_KEY,
        openrouter_model=raw_params.get("openRouterModel"),
    )

    if not params.url:
        is_closed = await send_ws_message(websocket, "error", "URL is required", is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

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

    pages_data = []
    for page in crawl_result.pages:
        pages_data.append(
            {
                "url": page.url,
                "path": page.path,
                "title": page.title,
                "html": page.html[:50000],
                "screenshot": page.screenshot,
                "forms": page.forms,
                "navigation": page.navigation,
                "images": page.images[:20],
                "depth": page.depth,
            }
        )

    crawl_payload = {
        "pagesFound": len(crawl_result.pages),
        "siteStructure": crawl_result.site_structure,
        "designTokens": crawl_result.design_tokens,
        "pages": [
            {"path": p["path"], "title": p["title"], "depth": p["depth"]}
            for p in pages_data
        ],
    }
    is_closed = await send_ws_message(websocket, "crawlComplete", json.dumps(crawl_payload), data=crawl_payload, is_closed=is_closed)

    is_closed = await send_ws_message(websocket, "status", "Generating project structure...", is_closed=is_closed)

    project_structure_prompt = build_project_structure_prompt(
        crawl_result, params.stack, params.generate_database, params.generate_auth
    )
    print(f"[URL2CODE] Project structure prompt length: {len(project_structure_prompt)} chars")

    project_structure = await _generate_with_llm(
        project_structure_prompt,
        params,
        websocket,
        is_closed,
        event_prefix="structure",
    )
    if project_structure is None:
        is_closed = await send_ws_message(websocket, "error", "Failed to generate project structure", is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    all_code: Dict[str, str] = {"project-structure": _clean_llm_output(project_structure)}

    if params.generate_database:
        is_closed = await send_ws_message(websocket, "status", "Generating database schema...", is_closed=is_closed)
        db_prompt = build_database_schema_prompt(crawl_result, params.stack)
        db_schema = await _generate_with_llm(
            db_prompt, params, websocket, is_closed, event_prefix="database"
        )
        if db_schema:
            all_code["database-schema"] = _clean_llm_output(db_schema)

    total_pages_to_gen = len(pages_data)
    for i, page_data in enumerate(pages_data):
        if is_closed:
            break

        page_path = page_data.get("path", "/")
        is_closed = await send_ws_message(
            websocket,
            "pageStart",
            f"Generating page {i + 1}/{total_pages_to_gen}: {page_path}",
            data={"pageIndex": i, "totalPages": total_pages_to_gen, "path": page_path},
            is_closed=is_closed,
        )

        page_code = await _run_agent_for_page(
            page_data, params, websocket, i, total_pages_to_gen
        )

        if page_code:
            all_code[page_path] = _clean_llm_output(page_code)
            is_closed = await send_ws_message(
                websocket,
                "pageComplete",
                f"Completed: {page_path}",
                data={"pageIndex": i, "totalPages": total_pages_to_gen, "path": page_path, "codeLength": len(page_code)},
                is_closed=is_closed,
            )
        else:
            is_closed = await send_ws_message(
                websocket,
                "pageComplete",
                f"Failed: {page_path}",
                data={"pageIndex": i, "totalPages": total_pages_to_gen, "path": page_path, "error": True},
                is_closed=is_closed,
            )

    print(f"[URL2CODE] Final: project-structure={len(all_code.get('project-structure', ''))} chars, pages={len([k for k in all_code if k != 'project-structure' and k != 'database-schema'])}")

    if not all_code.get("project-structure"):
        first_page_key = next(
            (k for k in all_code if k != "project-structure" and k != "database-schema" and all_code[k]),
            None,
        )
        if first_page_key:
            print(f"[URL2CODE] Using first page '{first_page_key}' as main code")
            all_code["project-structure"] = all_code[first_page_key]

    if not is_closed:
        final_payload = {
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
) -> str | None:
    openai_key = _get_api_key(params.openai_api_key, OPENAI_API_KEY)
    anthropic_key = _get_api_key(params.anthropic_api_key, ANTHROPIC_API_KEY)
    gemini_key = _get_api_key(params.gemini_api_key, GEMINI_API_KEY)
    openrouter_key = params.openrouter_api_key or OPENROUTER_API_KEY

    provider, model_id, api_key = _get_model_for_provider(params)
    print(f"[URL2CODE] LLM {event_prefix}: provider={provider}, model={model_id}, key={'set' if api_key else 'missing'}")

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Generate the code now."},
    ]

    try:
        if provider == "openrouter" and openrouter_key:
            result = await asyncio.wait_for(
                _call_openrouter(openrouter_key, model_id or "meta-llama/llama-3.3-70b-instruct:free", messages),
                timeout=300,
            )
            if not result:
                print(f"[LLM] Empty response from OpenRouter for {event_prefix}")
            return result
        elif provider == "anthropic" and anthropic_key:
            result = await asyncio.wait_for(
                _call_anthropic(anthropic_key, messages),
                timeout=300,
            )
            return result
        elif provider == "openai" and openai_key:
            result = await asyncio.wait_for(
                _call_openai(openai_key, params.openai_base_url, messages),
                timeout=300,
            )
            return result
        elif provider == "gemini" and gemini_key:
            result = await asyncio.wait_for(
                _call_gemini(gemini_key, messages),
                timeout=300,
            )
            return result
        else:
            print(f"No API key available for {event_prefix} generation")
            return None
    except asyncio.TimeoutError:
        print(f"Timeout in {event_prefix} generation after 300s")
        return None
    except Exception as e:
        print(f"Error in {event_prefix} generation: {e}")
        return None


async def _call_openrouter(
    api_key: str, model: str, messages: list[dict]
) -> str | None:
    print(f"[LLM] Calling OpenRouter model={model}")
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 16000,
            },
        )
        print(f"[LLM] OpenRouter response status={response.status_code}")
        if response.status_code == 200:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"[LLM] Response length={len(content)} chars, first 200: {content[:200]}")
            return content if content else None
        else:
            print(f"OpenRouter error: {response.status_code} {response.text[:500]}")
            return None


async def _call_anthropic(api_key: str, messages: list[dict]) -> str | None:
    print(f"[LLM] Calling Anthropic")
    system_msg = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            user_messages.append(msg)

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 16000,
                "system": system_msg,
                "messages": user_messages,
            },
        )
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            print(f"Anthropic error: {response.status_code} {response.text}")
            return None


async def _call_openai(
    api_key: str, base_url: str | None, messages: list[dict]
) -> str | None:
    print(f"[LLM] Calling OpenAI")
    url = (base_url or "https://api.openai.com/v1") + "/chat/completions"
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": messages,
                "max_tokens": 16000,
            },
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            print(f"OpenAI error: {response.status_code} {response.text}")
            return None


async def _call_gemini(api_key: str, messages: list[dict]) -> str | None:
    print(f"[LLM] Calling Gemini")
    system_msg = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            user_messages.append(msg["content"])

    contents = []
    if system_msg:
        contents.append({"role": "user", "parts": [{"text": system_msg}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    for um in user_messages:
        contents.append({"role": "user", "parts": [{"text": um}]})

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": contents},
        )
        if response.status_code == 200:
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            print(f"Gemini error: {response.status_code} {response.text}")
            return None
