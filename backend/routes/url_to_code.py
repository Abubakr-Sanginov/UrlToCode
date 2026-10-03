import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Set, cast
from urllib.parse import urlparse
from fastapi import APIRouter, Cookie, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
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
from crawler.media_store import MEDIA_ROUTE, rewrite_media_urls
import clone_pages
import clone_runs
import clone_routing
import llm_http
import vision
import clone_visual
import clone_backend
import clone_cache
import clone_mock
import clone_jobs
import clone_runner
import accounts as accounts_accounts
from routes import accounts as routes_accounts
from routes.telegram import announce_ready
from clone_runs import GENERATED_FILE_PREFIX, CloneRun, new_run_id
from prompts.framework_stacks import (
    FRAMEWORK_STACKS,
    build_head,
    build_route_map,
    clean_component_output,
    is_framework_stack,
    is_usable_component,
    route_for_path,
    wrap_component_preview,
)
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

# Framework stacks answer with a page component, not a document.
COMPONENT_USER_TURN = (
    "Output the complete .tsx file now, ending with the closing brace of the "
    "default export. No explanation, no plan, no markdown fences."
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
    # "", "low", "medium" or "high". Sent to reasoning models only when set;
    # left empty the provider decides, which is what most models expect.
    reasoning_effort: str | None = None
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
    # Capture every page at phone, tablet and desktop widths and build one
    # responsive layout from all three. Off by default: it roughly triples
    # the crawl's capture time.
    responsive: bool = False
    # Click the page's own controls and capture the states they reveal - a
    # mobile menu, a dialog, an opened accordion. The crawl acts instead of
    # only watching, so it is off unless asked for.
    capture_interactions: bool = False
    # Show the pages the crawl found and let the user pick which ones to
    # generate. Off by default: a three-page site is faster to just build.
    confirm_pages: bool = False
    # A hard limit in dollars for this run. Zero means no limit, which is
    # what an unset field arrives as - reading it as "spend nothing" would
    # break every run that did not set one.
    max_cost: float = 0.0
    # Let the summary and repair calls use a cheaper configured model. Pages
    # always use the chosen one.
    use_cheaper_models: bool = False
    # Extra attempts for the whole run after an answer that is empty or not a
    # usable file. Shared by every call site, see RetryBudget.
    max_retries: int = 1


class RetryBudget:
    """The run's retry allowance, in extra model calls.

    A run can answer badly in three places - the portal page, the single-file
    clone, and any individual page - and each of them used to get its own
    `max_retries`, so the number in Advanced options multiplied by the number
    of call sites. One budget for the run keeps the setting honest and caps
    what a bad model can cost.

    Pages are generated concurrently and race for what is left.
    """

    def __init__(self, total: int) -> None:
        self.total = max(0, total)
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.total - self.used

    def take(self) -> bool:
        """Claim one retry, or report that the run has run out of them."""
        # No await in here, so concurrent pages cannot claim the same retry.
        if self.used >= self.total:
            return False
        self.used += 1
        return True


UrlMessageType = Literal[
    "status",
    "progress",
    "crawlComplete",
    "pageStart",
    "pageComplete",
    "pageSelection",
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
            model=params.openrouter_model or OPENROUTER_MODEL or "",
            api_key=openrouter_key,
            reasoning_effort=params.reasoning_effort or llm_http.DEFAULT_REASONING_EFFORT,
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
    user_turn = (
        COMPONENT_USER_TURN if is_framework_stack(params.stack) else GENERATE_USER_TURN
    )

    try:
        return await complete(_llm_config_for(params), system_msg, user_turn)
    except ProviderError:
        # Per-page failures are reported individually, but a provider-level
        # refusal (retired model, bad key, rate limit) will hit every page, so
        # let it abort the run with the real reason.
        raise
    except Exception as e:
        # Never report an empty reason. An exception with no message of its
        # own - httpx's are all like this - produces a blank line in the log
        # and a blank error in the app, which is the one thing the user
        # cannot act on.
        reason = str(e) or type(e).__name__
        print(f"Error generating page {page_data.get('path', '?')}: {reason}")
        return Completion(empty_reason=reason)


# Pages are generated concurrently; more than a handful of parallel calls
# gets free tiers rate limited, which costs more time than it saves.
PAGE_CONCURRENCY = 3

MAX_PAGES = 30
MAX_DEPTH = 6
DEFAULT_PAGES = 10
DEFAULT_DEPTH = 2
# How long the server waits for the user to pick pages before giving up on
# the run. Long enough to read a list of thirty pages and think about it;
# short enough that a socket is not held open overnight.
PAGE_SELECTION_TIMEOUT_SECONDS = 600
MAX_RETRIES = 5
DEFAULT_RETRIES = 1
# The most a single run may be allowed to spend. A ceiling above this is
# almost certainly a typo - "500" meaning cents, say - and honouring it
# would remove the only guard the user has.
MAX_RUN_COST = 500.0
VALID_STACKS = frozenset(
    {
        "html_tailwind",
        "html_css",
        "react_tailwind",
        "bootstrap",
        "vue_tailwind",
        "ionic_tailwind",
        *FRAMEWORK_STACKS,
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


def _clamp_float(raw: Any, default: float, low: float, high: float) -> float:
    """Coerce an untrusted websocket value into `low..high`.

    A non-numeric or missing value falls back to `default`. A cost ceiling
    that arrived as a string, or as nothing at all, must not become a run
    that spends nothing or a run with no limit at all.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN, which compares false against everything
        return default
    return max(low, min(value, high))


def _normalize_stack(raw: Any) -> str:
    stack = str(raw or "").replace("-", "_")
    return stack if stack in VALID_STACKS else "html_tailwind"


def _http_origin(websocket: WebSocket) -> str:
    """Origin the client reaches this backend on, as an http(s) URL."""
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    return f"{scheme}://{websocket.url.netloc}"


def _request_base_url(request: Request) -> str:
    """Origin this backend is reached on, from the HTTP request itself."""
    return str(request.base_url).rstrip("/")


class RegeneratePageRequest(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    # Present when this is a repair rather than a plain redo: what the visual
    # check or the user wants changed about the page as it stands.
    instruction: str | None = Field(default=None, max_length=4000)
    # The generation settings, so a regeneration uses the same stack and the
    # client's own provider keys. Keys are never stored on the run.
    stack: str | None = None
    openaiApiKey: str | None = None
    openaiBaseURL: str | None = None
    anthropicApiKey: str | None = None
    anthropicModel: str | None = None
    geminiApiKey: str | None = None
    geminiModel: str | None = None
    openRouterApiKey: str | None = None
    openRouterModel: str | None = None
    # Reasoning models spend their whole budget thinking; low is what made a
    # page come back at all. Empty leaves the provider to decide.
    reasoningEffort: str | None = None
    openAiModel: str | None = None
    customProviderBaseUrl: str | None = None
    customProviderApiKey: str | None = None
    customProviderModel: str | None = None
    generateDatabase: bool | None = None


class RegeneratePageResponse(BaseModel):
    path: str
    code: str
    error: str = ""


class VisualCheckRequest(BaseModel):
    # Omitted means every page of the run.
    paths: List[str] | None = None
    # Below this a page counts as wrong. Left unset uses the module default.
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class RepairRequest(BaseModel):
    paths: List[str] | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    # Extra generations for one page before moving on to the next.
    maxAttempts: int = 2
    # How many pages one call may repair, so the cost stays predictable.
    maxPages: int = 3
    openaiApiKey: str | None = None
    openRouterApiKey: str | None = None
    openRouterModel: str | None = None
    # Reasoning models spend their whole budget thinking; low is what made a
    # page come back at all. Empty leaves the provider to decide.
    reasoningEffort: str | None = None
    anthropicApiKey: str | None = None
    geminiApiKey: str | None = None
    customProviderBaseUrl: str | None = None
    customProviderApiKey: str | None = None
    customProviderModel: str | None = None


@router.get("/api/clone-runs")
async def list_clone_runs(limit: int = 50) -> Dict[str, Any]:
    """Stored clone runs, newest first, for the clone library."""
    return {"runs": clone_runs.list_runs(max(1, min(limit, 200)))}


@router.get("/api/clone-runs/{run_id}")
async def get_clone_run(run_id: str) -> Dict[str, Any]:
    """One stored run, including every page's code and status."""
    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such clone run.")
    return {
        "runId": run.run_id,
        "baseUrl": run.base_url,
        "stack": run.stack,
        "params": run.params,
        "llm": run.llm,
        "phase": run.phase,
        "error": run.error,
        "createdAt": run.created_at,
        "updatedAt": run.updated_at,
        "pages": {path: page.to_json() for path, page in run.pages.items()},
        "code": run.code_map(),
    }


class CostEstimateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    maxPages: int = Field(default=10, ge=1, le=MAX_PAGES)
    openaiApiKey: str | None = None
    openaiModel: str | None = None
    anthropicApiKey: str | None = None
    anthropicModel: str | None = None
    geminiApiKey: str | None = None
    geminiModel: str | None = None
    openRouterApiKey: str | None = None
    openRouterModel: str | None = None
    # Reasoning models spend their whole budget thinking; low is what made a
    # page come back at all. Empty leaves the provider to decide.
    reasoningEffort: str | None = None
    # A user's own endpoint. Carried here too, because a user whose only
    # provider is their own would otherwise get an estimate - and a vision
    # answer - about a model they did not choose.
    customProviderApiKey: str | None = None
    customProviderBaseUrl: str | None = None
    customProviderModel: str | None = None


class VisionRequest(BaseModel):
    openaiApiKey: str | None = None
    openaiModel: str | None = None
    anthropicApiKey: str | None = None
    anthropicModel: str | None = None
    geminiApiKey: str | None = None
    geminiModel: str | None = None
    openRouterApiKey: str | None = None
    openRouterModel: str | None = None
    # Reasoning models spend their whole budget thinking; low is what made a
    # page come back at all. Empty leaves the provider to decide.
    reasoningEffort: str | None = None
    customProviderApiKey: str | None = None
    customProviderBaseUrl: str | None = None
    customProviderModel: str | None = None
    # Skip the remembered answer and ask the model again.
    refresh: bool = False


@router.post("/api/clone-vision")
async def detect_clone_vision(body: VisionRequest) -> Dict[str, Any]:
    """Whether the chosen model can see images.

    Asked by asking: a picture with a checkable question is sent to the
    model, and what it answers is the answer. The name list is only used
    when there is no key to ask with, because "supports vision" is a
    property of the deployment as much as of the name - a gateway can route
    the same id to something that does not see.
    """
    cfg = _llm_config_for(UrlToCodeParams(url="https://vision-check.invalid", **_vision_params(body)))
    answer = await vision.detect(cfg, force=body.refresh)
    return answer.to_json()


def _vision_params(body: VisionRequest) -> Dict[str, Any]:
    return {
        "openai_api_key": body.openaiApiKey,
        "openai_model": body.openaiModel,
        "anthropic_api_key": body.anthropicApiKey,
        "anthropic_model": body.anthropicModel,
        "gemini_api_key": body.geminiApiKey,
        "gemini_model": body.geminiModel,
        "openrouter_api_key": body.openRouterApiKey,
        "openrouter_model": body.openRouterModel,
        "reasoning_effort": body.reasoningEffort,
        "custom_provider_api_key": body.customProviderApiKey,
        "custom_provider_base_url": body.customProviderBaseUrl,
        "custom_provider_model": body.customProviderModel,
    }


@router.post("/api/clone-cost/estimate")
async def estimate_clone_cost(body: CostEstimateRequest) -> Dict[str, Any]:
    """What a clone of this size is likely to cost, before it starts.

    An estimate, not a quote: a page with a lot of markup in it costs more
    than one with little, and a page that comes back short costs less. It is
    here because this is the only moment the number is still actionable -
    afterwards the money is spent whatever the user learns.
    """
    params = UrlToCodeParams(
        url=body.url,
        openai_api_key=body.openaiApiKey,
        anthropic_api_key=body.anthropicApiKey,
        gemini_api_key=body.geminiApiKey,
        openrouter_api_key=body.openRouterApiKey,
        openai_model=body.openaiModel,
        anthropic_model=body.anthropicModel,
        gemini_model=body.geminiModel,
        openrouter_model=body.openRouterModel,
        reasoning_effort=body.reasoningEffort,
        custom_provider_api_key=body.customProviderApiKey,
        custom_provider_base_url=body.customProviderBaseUrl,
        custom_provider_model=body.customProviderModel,
    )
    chosen = _llm_config_for(params)
    # Read from the answer already remembered, never ask here. The media on
    # a cloned site is downloaded rather than generated, so whether the model
    # can see has no bearing on what a page costs - and asking it means an
    # extra round trip to a provider that may take minutes to answer at all.
    # A generation must not wait on a question only the repair needs.
    known = vision.cached(chosen.model, chosen.base_url)
    vision_answer = known or vision.VisionAnswer(
        model=chosen.model or "",
        capability=vision.UNKNOWN,
        source="name",
        detail="not checked yet",
    )
    routing = clone_routing.route(
        params.__dict__, chosen, vision_capability=vision_answer.capability
    )
    pages = clone_routing.estimate(
        clone_routing.config_for(chosen, routing.page_model), body.maxPages
    )
    return {
        "pages": pages,
        "routing": routing.to_json(),
        "vision": vision_answer.to_json(),
    }


@router.get("/api/clone-cache")
async def clone_cache_stats() -> Dict[str, Any]:
    """What the generation cache is holding, and where.

    Offered because a cache the user cannot see or empty is a cache that
    starts looking like the tool is wrong: the same site producing a
    different page needs an explanation the user can act on.
    """
    return clone_cache.stats()


@router.delete("/api/clone-cache")
async def clear_clone_cache() -> Dict[str, Any]:
    """Empty the generation cache.

    Every page is generated from the site's own crawl again, so this costs a
    model call per page the next time round - which is exactly why it is the
    user's call and not something the backend does to itself.
    """
    removed = clone_cache.clear()
    return {"removed": removed}


@router.get("/api/clone-runs/{run_id}/status")
async def clone_run_status(run_id: str) -> Dict[str, Any]:
    """Where a run is, and how much of it is still to do.

    Separate from the run itself because this is what a page that has been
    closed and reopened asks for: not the code, but whether anything is
    still happening and what to pick up if not.
    """
    return clone_runner.status(run_id)


class ResumeRunRequest(BaseModel):
    # The key is never stored: the run keeps the provider and model it was
    # started with, and a resumed page has to look like the rest of the run.
    # Field names are camelCase to match the rest of this API.
    openaiApiKey: str | None = None
    anthropicApiKey: str | None = None
    geminiApiKey: str | None = None
    openRouterApiKey: str | None = None
    replicateApiKey: str | None = None
    customProviderApiKey: str | None = None


@router.post("/api/clone-runs/{run_id}/resume")
async def resume_clone_run(run_id: str, body: ResumeRunRequest, request: Request) -> Dict[str, Any]:
    """Carry a stopped run on from whatever is already done.

    The run keeps its crawl, so this generates only the pages with no code
    yet rather than starting the whole clone again - which is the difference
    between finishing a run that got halfway and paying for it twice.
    """
    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such clone run.")

    cfg = LlmConfig(
        provider=str(run.llm.get("provider") or "none"),
        model=str(run.llm.get("model") or ""),
        # The key comes from this request and is never written down: the run
        # remembers which provider answered, not how to reach it.
        api_key=_key_for_provider(run.llm.get("provider"), body) or "",
        base_url=str(run.llm.get("base_url") or ""),
    )
    # The same origin the first run used, so a resumed page points its images
    # at the same place as the pages generated before it.
    status = clone_runner.resume(run_id, cfg, _request_base_url(request))
    if status.get("error"):
        raise HTTPException(status_code=409, detail=str(status["error"]))
    return status


@router.post("/api/clone-runs/{run_id}/stop")
async def stop_clone_run(run_id: str) -> Dict[str, Any]:
    """Stop a run that is still going, keeping everything it has done."""
    stopped = clone_jobs.registry.cancel(run_id)
    return {"runId": run_id, "stopped": stopped}


def _key_for_provider(provider: Any, body: "ResumeRunRequest") -> str | None:
    """The key that belongs to the provider this run was started with.

    A run records which provider answered, so a resumed page has to be sent
    with that provider's key and not whichever one the Settings dialog
    happens to have filled in most recently.
    """
    name = str(provider or "").lower()
    if name == "openai":
        return body.openaiApiKey or OPENAI_API_KEY
    if name == "anthropic":
        return body.anthropicApiKey or ANTHROPIC_API_KEY
    if name == "gemini":
        return body.geminiApiKey or GEMINI_API_KEY
    if name == "openrouter":
        return body.openRouterApiKey or OPENROUTER_API_KEY
    if name == "replicate":
        return body.replicateApiKey or REPLICATE_API_KEY
    if name == "custom":
        return body.customProviderApiKey
    return None


@router.post("/api/clone-runs/{run_id}/regenerate-page")
async def regenerate_clone_page(
    run_id: str,
    body: RegeneratePageRequest,
    request: Request,
    utc_session: str | None = Cookie(default=None),
) -> RegeneratePageResponse:
    """Generate one page of a stored run again.

    The run keeps the crawl, so a single page can be rebuilt without crawling
    the site again and without regenerating the pages around it. This is also
    what the visual check calls when a page comes back too different from its
    original.

    Charged one unit, not one per page: the run the visual check calls this
    from repairs several pages, and billing each one separately would charge
    the user for a single repair they asked for once.
    """
    account = routes_accounts.require_account(utc_session)
    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such clone run.")

    existing = run.pages.get(body.path)
    if body.path != "project-structure" and run.crawl is not None and not any(
        page.path == body.path for page in run.crawl.pages
    ):
        raise HTTPException(
            status_code=404, detail=f"This run has no crawled page {body.path!r}."
        )

    # The run's stack is the one its pages were generated for; a stack in the
    # request is only honoured when the run never recorded one.
    if not run.stack and body.stack:
        run.stack = _normalize_stack(body.stack)

    cfg = _llm_config_for(
        UrlToCodeParams(
            url=run.base_url,
            stack=run.stack,
            max_pages=int(run.params.get("maxPages", MAX_PAGES) or MAX_PAGES),
            max_depth=int(run.params.get("maxDepth", DEFAULT_DEPTH) or DEFAULT_DEPTH),
            openai_api_key=body.openaiApiKey,
            anthropic_api_key=body.anthropicApiKey,
            gemini_api_key=body.geminiApiKey,
            openrouter_api_key=body.openRouterApiKey,
            openrouter_model=body.openRouterModel,
            reasoning_effort=body.reasoningEffort,
            openai_model=body.openAiModel,
            openai_base_url=body.openaiBaseURL,
            anthropic_model=body.anthropicModel,
            gemini_model=body.geminiModel,
            custom_provider_base_url=body.customProviderBaseUrl,
            custom_provider_api_key=body.customProviderApiKey,
            custom_provider_model=body.customProviderModel,
            generate_database=(
                body.generateDatabase
                if body.generateDatabase is not None
                else bool(run.params.get("generateDatabase"))
            ),
        )
    )
    if not cfg.is_usable:
        raise HTTPException(
            status_code=400,
            detail="No model provider configured. Add a key in Settings.",
        )

    if body.path == "project-structure":
        # The portal is not a crawled page, so it has no stored page data.
        raise HTTPException(
            status_code=400, detail="The landing page is generated with the run."
        )

    try:
        usage = accounts_accounts.spend_action(account, "generate")
    except accounts_accounts.NoCapacity as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    code, error = await clone_pages.generate_page(
        run,
        body.path,
        cfg,
        _request_base_url(request),
        instruction=body.instruction or "",
        existing_code=(existing.code if existing else ""),
    )
    if not code:
        # Nothing usable came back, so the unit is handed back. Taking it
        # up front is what keeps a limit a limit; returning it here is
        # what stops a failed page from costing a day's allowance.
        accounts_accounts.refund_action(account, "generate")
        if existing is not None:
            record = run.page(body.path)
            record.error = error
            record.attempts += 1
            try:
                clone_runs.save_run(run)
            except OSError:
                pass
        raise HTTPException(status_code=502, detail=error or "Generation failed.")

    record = run.page(body.path)
    record.status = clone_runs.COMPLETE
    record.code = code
    record.error = ""
    record.attempts += 1
    try:
        clone_runs.save_run(run)
    except OSError as exc:
        # The page is generated either way; the client can keep the code it
        # was handed even if the record could not be written.
        print(f"[URL2CODE] Could not save regenerated page {body.path}: {exc}")

    return RegeneratePageResponse(path=body.path, code=code)


@router.post("/api/clone-runs/{run_id}/visual-check")
async def visual_check_clone_run(
    run_id: str, body: VisualCheckRequest
) -> Dict[str, Any]:
    """Render a run's pages and score them against the crawled originals.

    The originals were captured as full-page screenshots while crawling, so
    this compares the clone with the real site rather than with a second
    opinion. The score is recorded on the run, which is what the version list
    and "fix this page" read.
    """
    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such clone run.")

    requested = [path for path in (body.paths or []) if path]
    unknown = [path for path in requested if path not in run.pages]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"This run has no page {unknown[0]!r}.",
        )

    check = await clone_visual.check_run(
        run,
        paths=requested or None,
        threshold=body.threshold
        if body.threshold is not None
        else clone_visual.DEFAULT_REPAIR_THRESHOLD,
    )
    return check.to_json()


def _usage_json(usage: accounts_accounts.Usage) -> Dict[str, Any]:
    """What is left after an action, so the UI does not have to guess."""
    return {
        "used": usage.used,
        "remaining": usage.remaining,
        # Carried here as well as with the runs: a clone makes a project
        # as it finishes, and the panel's "0 of 1" would be wrong the
        # moment after it says the clone worked.
        "projects": usage.projects,
        "maxProjects": usage.max_projects,
        "tier": usage.tier,
        "resetsAt": usage.resets_at,
    }


@router.post("/api/clone-runs/{run_id}/repair")
async def repair_clone_run(
    run_id: str,
    body: RepairRequest,
    request: Request,
    utc_session: str | None = Cookie(default=None),
) -> Dict[str, Any]:
    """Regenerate the pages that scored below the fidelity threshold.

    Each page is regenerated from the run's stored crawl with an instruction
    built from where the render actually differed, so the model is told what
    to change instead of being asked for a second guess at the whole page.
    Pages are repaired one at a time and re-checked, and the loop stops as soon
    as a page passes, so a page that is already right is not paid for twice.

    The whole repair is charged once, however many pages it goes through:
    it is one click, and billing it per page would make a three page site
    cost three units before the user had seen anything.
    """
    account = routes_accounts.require_account(utc_session)
    run = clone_runs.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such clone run.")

    threshold = (
        body.threshold
        if body.threshold is not None
        else clone_visual.DEFAULT_REPAIR_THRESHOLD
    )
    check = await clone_visual.check_run(run, paths=body.paths or None, threshold=threshold)
    if check.skipped and not check.pages:
        return {
            "runId": run.run_id,
            "repaired": [],
            "skipped": check.skipped,
            "error": "No page of this run could be rendered here, so none can be checked or repaired.",
        }

    cfg = _llm_config_for(
        UrlToCodeParams(
            url=run.base_url,
            stack=run.stack,
            openai_api_key=body.openaiApiKey,
            anthropic_api_key=body.anthropicApiKey,
            gemini_api_key=body.geminiApiKey,
            openrouter_api_key=body.openRouterApiKey,
            openrouter_model=body.openRouterModel,
            reasoning_effort=body.reasoningEffort,
            custom_provider_base_url=body.customProviderBaseUrl,
            custom_provider_api_key=body.customProviderApiKey,
            custom_provider_model=body.customProviderModel,
            generate_database=bool(run.params.get("generateDatabase")),
        )
    )
    if not cfg.is_usable:
        raise HTTPException(
            status_code=400,
            detail="No model provider configured. Add a key in Settings.",
        )

    # One click, one unit, whatever the loop below goes on to spend. Taken
    # here rather than after the work because by then the model calls are
    # already paid for, and a limit that is checked late is not a limit.
    try:
        usage = accounts_accounts.spend_action(account, "edit")
    except accounts_accounts.NoCapacity as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    attempts = max(1, min(body.maxAttempts, 3))
    budget = max(1, min(body.maxPages, 10))
    media_base = _request_base_url(request)
    # Whether this model can be shown the screenshots the repair is made
    # from. Asked once per repair rather than per page, and cached per
    # model, because it is the difference between a fix and a guess.
    vision_answer = await vision.detect(cfg)
    sees_images = vision_answer.sees_images
    repaired: List[Dict[str, Any]] = []
    stopped = ""

    for page in check.below_threshold():
        if len(repaired) >= budget:
            # A site-wide mistake produces a run of failing pages; repairing
            # all of them one after another is a lot of model calls for the
            # same fault, so the caller is told where it stopped.
            stopped = (
                f"Repaired {budget} of "
                f"{len(check.below_threshold())} pages below the threshold. "
                "Run it again to continue."
            )
            break
        path = page.path
        record = run.pages.get(path)
        # A model that can see is shown the two screenshots; one that cannot
        # is given the description, because a model handed a picture it
        # cannot read answers from the words around it and produces a
        # confident wrong fix.
        pictures = (
            clone_visual.repair_images(page) if sees_images else []
        )
        instruction = clone_visual.repair_instruction(page, 0)
        if pictures:
            instruction += clone_visual.describe_repair_images(pictures)
        for attempt_index in range(attempts):
            code, error = await clone_pages.generate_page(
                run,
                path,
                cfg,
                media_base,
                instruction=instruction,
                existing_code=(record.code if record else ""),
                images=pictures,
            )
            if not code:
                print(f"[REPAIR] {path} attempt {attempt_index + 1} failed: {error}")
                break

            if record is not None:
                record.code = code
                record.status = clone_runs.COMPLETE
                record.error = ""
                record.attempts += 1
            try:
                clone_runs.save_run(run, prune=False)
            except OSError as exc:
                print(f"[REPAIR] Could not save {path}: {exc}")

            recheck = await clone_visual.check_page(
                run, path, code, page.original_file
            )
            score = recheck.result.score
            if recheck.result.ok and record is not None:
                record.fidelity = score
            try:
                clone_runs.save_run(run)
            except OSError as exc:
                print(f"[REPAIR] Could not record the score for {path}: {exc}")

            repaired.append(
                {
                    "path": path,
                    "attempt": attempt_index + 1,
                    "score": score,
                    "passed": recheck.result.ok and score >= threshold,
                    "error": recheck.result.error,
                }
            )
            if recheck.result.ok and score >= threshold:
                break
            # The page still does not match after this attempt; point the next
            # one at what is still wrong rather than repeating the first hint.
            page = recheck
            # And show it the new render, so the next attempt is made from
            # what the page looks like now rather than from what it did.
            pictures = (
                clone_visual.repair_images(page) if sees_images else []
            )
            instruction = clone_visual.repair_instruction(page, attempt_index + 1)
            if pictures:
                instruction += clone_visual.describe_repair_images(pictures)

    # Nothing came back better than it started, so the repair did nothing
    # for the user. The unit is returned rather than charged for a pass
    # that changed no page.
    if not repaired:
        accounts_accounts.refund_action(account, "edit")
        usage = accounts_accounts.usage_of(account, "edit")

    return {
        "runId": run.run_id,
        "repaired": repaired,
        "skipped": check.skipped,
        "stopped": stopped,
        "code": run.code_map(),
        # Reported so the UI can say why a repair was made from a
        # description rather than from the screenshots.
        "vision": vision_answer.to_json(),
        "usage": _usage_json(usage),
    }


def _site_name_for(crawl: CrawlResult) -> str:
    """A name for the clone, from the site itself.

    Only a title, never a URL or a file name: this ends up in a generated
    Python string literal and a README heading, and a hostname like
    `example.com/page` reads badly in both.
    """
    for page in crawl.pages:
        title = (page.title or "").strip()
        # A browser's default title is the address bar, which says nothing.
        if title and not title.lower().startswith(("http://", "https://")):
            return title[:60]
    return "the site"


def _create_run(
    params: UrlToCodeParams,
    crawl_result: CrawlResult,
    page_paths: List[str],
) -> CloneRun:
    """Start a stored run for this crawl.

    The API keys are deliberately left out: they live in the client's settings
    and are sent again on every request, and a key must never reach the disk.
    """
    llm_cfg = _llm_config_for(params)
    run = CloneRun(
        run_id=new_run_id(),
        base_url=crawl_result.base_url,
        stack=params.stack,
        params={
            "maxPages": params.max_pages,
            "maxDepth": params.max_depth,
            "maxRetries": params.max_retries,
            "generateDatabase": params.generate_database,
            "generateAuth": params.generate_auth,
            "singleFile": params.single_file,
            "responsive": params.responsive,
            "captureInteractions": params.capture_interactions,
            "confirmPages": params.confirm_pages,
            "maxCost": params.max_cost,
        },
        llm={"provider": llm_cfg.provider, "model": llm_cfg.model or ""},
        crawl=crawl_result,
    )
    for path in page_paths:
        run.page(path)
    return run


@router.websocket("/url-to-code/{run_id}/follow")
async def follow_clone_run_ws(websocket: WebSocket, run_id: str):
    """Watch a run that is already going.

    Opening a second tab, or coming back to one that was closed, attaches to
    the work rather than starting it again. The run may not be generating at
    all - the client asks `/status` for what is still missing and offers to
    finish it.
    """
    await websocket.accept()
    try:
        run = clone_runs.load_run(run_id)
    except Exception:
        run = None
    if run is None:
        await send_ws_message(websocket, "error", "No such clone run.")
        await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    subscriber = clone_jobs.registry.subscribe(run_id)
    try:
        # What the run already has, before anything live: a client that
        # attaches halfway through still sees the pages that finished first.
        for path, page in run.pages.items():
            if not page.code:
                continue
            await send_ws_message(
                websocket,
                "pageComplete",
                f"Completed: {path}",
                data={"path": path, "codeLength": len(page.code)},
            )
        if run.code_map():
            await send_ws_message(
                websocket,
                "partialCode",
                f"{len(run.code_map())} files ready",
                data={"code": run.code_map()},
            )

        async for event in subscriber:
            if event is None:
                break
            await send_ws_message(
                websocket,
                cast(UrlMessageType, event.type),
                event.value,
                data=event.data,
            )
    except (ConnectionClosedOK, ConnectionClosedError, WebSocketDisconnect):
        pass
    finally:
        # Leaving does not stop the run: that is the whole point. The job
        # carries on and the next client attaches to whatever is left.
        clone_jobs.registry.unsubscribe(run_id, subscriber)
        try:
            await websocket.close()
        except RuntimeError:
            pass


async def _stop_run(websocket: WebSocket, is_closed: bool, reason: str) -> None:
    """Say why the run stopped, and close the socket.

    The close is the point. Returning from the handler without it leaves the
    client reading a stream that will never carry another message, and the
    only way out is for the user to notice their browser is spinning.
    """
    await send_ws_message(websocket, "status", reason, is_closed=is_closed)
    try:
        await websocket.close()
    except (ConnectionClosedOK, ConnectionClosedError, RuntimeError, WebSocketDisconnect):
        pass


async def _await_page_selection(
    websocket: WebSocket, pages: List[CrawlPage]
) -> set[str] | None:
    """Ask which of the crawled pages to build, and wait for the answer.

    Returns the chosen paths, or None when the client never answered - which
    is different from an empty set, that one being the user saying "none of
    these". Both are honoured; only the second one means carry on.

    The wait is bounded. A user who walked away mid-crawl should find a
    finished or abandoned run when they come back, not a socket held open
    forever holding a job slot.
    """
    listing: List[Dict[str, Any]] = [
        {
            "path": page.path,
            "title": page.title,
            "url": page.url,
            "depth": page.depth,
            "screenshot": page.screenshot,
        }
        for page in pages
    ]
    await send_ws_message(
        websocket,
        "pageSelection",
        f"{len(pages)} pages found вЂ” choose which to build",
        data={"pages": listing, "timeoutSeconds": PAGE_SELECTION_TIMEOUT_SECONDS},
    )

    try:
        raw = await asyncio.wait_for(
            websocket.receive_json(), timeout=PAGE_SELECTION_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        print("[URL2CODE] Page selection timed out")
        return None
    except (WebSocketDisconnect, ConnectionClosedOK, ConnectionClosedError, RuntimeError, ValueError):
        return None

    if not isinstance(raw, dict):
        return None
    payload: Dict[str, Any] = cast(Dict[str, Any], raw)
    chosen = payload.get("paths")
    if not isinstance(chosen, list):
        return None
    # Only paths the crawl actually found: a client that names a page nobody
    # crawled would otherwise get an empty generation with no explanation.
    known = {page.path for page in pages}
    picked: Set[str] = set()
    named: List[Any] = list(chosen)
    for value in named:
        if isinstance(value, str) and value in known:
            picked.add(value)
    return picked


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
        max_retries=_clamp_int(raw_params.get("maxRetries"), DEFAULT_RETRIES, 0, MAX_RETRIES),
        openai_api_key=raw_params.get("openAiApiKey"),
        anthropic_api_key=raw_params.get("anthropicApiKey"),
        gemini_api_key=raw_params.get("geminiApiKey"),
        replicate_api_key=raw_params.get("replicateApiKey"),
        openai_base_url=raw_params.get("openAiBaseURL"),
        generate_database=bool(raw_params.get("generateDatabase", False)),
        generate_auth=bool(raw_params.get("generateAuth", False)),
        openrouter_api_key=raw_params.get("openRouterApiKey") or OPENROUTER_API_KEY,
        openrouter_model=raw_params.get("openRouterModel"),
        reasoning_effort=raw_params.get("reasoningEffort"),
        anthropic_model=raw_params.get("anthropicModel"),
        openai_model=raw_params.get("openAiModel"),
        gemini_model=raw_params.get("geminiModel"),
        # Custom OpenAI-compatible provider
        custom_provider_base_url=raw_params.get("customProviderBaseUrl"),
        custom_provider_api_key=raw_params.get("customProviderApiKey"),
        custom_provider_model=raw_params.get("customProviderModel"),
        single_file=bool(raw_params.get("singleFile", False)),
        responsive=bool(raw_params.get("responsive", False)),
        capture_interactions=bool(raw_params.get("captureInteractions", False)),
        confirm_pages=bool(raw_params.get("confirmPages", False)),
        max_cost=_clamp_float(raw_params.get("maxCost"), 0.0, 0.0, MAX_RUN_COST),
        use_cheaper_models=bool(raw_params.get("useCheaperModels", False)),
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

    # Taken before the crawl, which is the slowest part of a run: telling
    # someone they have used their day's allowance only after they have
    # waited out a whole crawl is the worst possible moment to say it.
    #
    # The cookie is tried first, and the same token is accepted in the
    # params message because a browser cannot put a cookie on a websocket
    # handshake to another origin - the app is served from a different port
    # than the API - and there is no way to ask it to. The token is the
    # same signed value either way, verified by the same function.
    account = routes_accounts.current_account(
        websocket.cookies.get(routes_accounts.SESSION_COOKIE)
    ) or routes_accounts.current_account(
        str(raw_params.get("sessionToken") or "") or None
    )
    if account is None:
        is_closed = await send_ws_message(
            websocket,
            "error",
            "Sign in to run a clone. Every account gets one run a day to try it.",
            is_closed=is_closed,
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    try:
        run_usage = accounts_accounts.spend_action(account, "generate")
    except accounts_accounts.NoCapacity as exc:
        is_closed = await send_ws_message(
            websocket, "error", str(exc), is_closed=is_closed
        )
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # One allowance for the whole run: whichever call runs out of usable
    # answers first spends it, and the rest of the run gets no second chance.
    retry_budget = RetryBudget(params.max_retries)

    # The crawl is strictly observational: pages are loaded, waited out and
    # captured as a visitor sees them вЂ” no clicks, no typing, no submissions.
    # Generation is driven purely by the captured markup.

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
        # Shown next to the clone so it can be compared with the original.
        capture_screenshots=True,
        responsive=params.responsive,
        capture_states=params.capture_interactions,
    )
    crawler.set_progress_callback(crawl_progress)

    is_closed = await send_ws_message(websocket, "status", "Starting website crawl...", is_closed=is_closed)

    try:
        print(f"[URL2CODE] Starting crawl of {params.url}")
        crawl_result = await crawler.crawl(params.url)
        print(f"[URL2CODE] Crawl complete: {len(crawl_result.pages)} pages, error={crawl_result.error}")
    except Exception as e:
        print(f"[URL2CODE] Crawl exception: {e}")
        # The crawl never produced a page, so the run produced nothing.
        # The allowance goes back rather than being spent on a dead URL.
        accounts_accounts.refund_action(account, "generate")
        is_closed = await send_ws_message(websocket, "error", f"Crawl failed: {str(e)}", is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    if crawl_result.error:
        accounts_accounts.refund_action(account, "generate")
        is_closed = await send_ws_message(websocket, "error", crawl_result.error, is_closed=is_closed)
        if not is_closed:
            await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
        return

    # A site behind a bot check hands back "Just a moment..." for every page.
    # Generating from that produces a blank clone, so say what happened.
    if crawl_result.pages and all(page.blocked for page in crawl_result.pages):
        accounts_accounts.refund_action(account, "generate")
        is_closed = await send_ws_message(
            websocket,
            "error",
            "The site is behind a bot check (Cloudflare) and served a "
            "verification page instead of its content. Cloning it is not "
            "possible from here вЂ” try a different URL.",
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
  <footer><p>В© 2024 {site_name}. All rights reserved.</p></footer>
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
                "images": page.images[:20],
                "videos": page.videos,
                "design": page.design,
                "meta": page.meta,
                "depth": page.depth,
            }
        )

    # From here on the run exists on disk: the crawl that produced it cannot
    # be re-derived cheaply, and per-page regeneration, fidelity scoring and
    # resuming an interrupted clone all need it after this socket is gone.
    run = _create_run(params, crawl_result, [page["path"] for page in pages_data])

    def checkpoint(phase: str | None = None) -> None:
        if phase is not None:
            run.phase = phase
        clone_runs.save_run(run, prune=False)

    try:
        checkpoint("generating")
    except OSError as exc:
        # Losing the ability to checkpoint should not lose the run the user
        # is watching; the clone still completes over the socket.
        print(f"[URL2CODE] Could not checkpoint run {run.run_id}: {exc}")

    # Captured images and videos, served by this backend: the clone shows
    # the copies taken while crawling instead of hotlinking the live site.
    all_media: Dict[str, str] = {}
    for page in crawl_result.pages:
        all_media.update(page.media)
    media_base_url = _http_origin(websocket)
    if all_media:
        print(f"[URL2CODE] {len(all_media)} media files captured")

    def localize(code: str) -> str:
        return rewrite_media_urls(code, all_media, media_base_url) if all_media else code

    # Pages link to each other through their local files (or routes, for a
    # framework project), so the saved project navigates like the original
    # instead of leaving for the live site.
    is_framework = is_framework_stack(params.stack)
    link_map = (
        build_route_map(p["path"] for p in pages_data)
        if is_framework
        else build_link_map(p["path"] for p in pages_data)
    )

    # The original of every page, for side-by-side comparison in the UI.
    screenshots = {
        page.path: f"{media_base_url}{MEDIA_ROUTE}/{page.screenshot_file}"
        for page in crawl_result.pages
        if page.screenshot_file
    }
    crawl_payload: Dict[str, Any] = {
        "pagesFound": len(crawl_result.pages),
        "baseUrl": crawl_result.base_url,
        "runId": run.run_id,
        "screenshots": screenshots,
        "sourceUrls": {page.path: page.url for page in crawl_result.pages},
        "siteStructure": crawl_result.site_structure,
        "designTokens": crawl_result.design_tokens,
        "pages": [
            {"path": p["path"], "title": p["title"], "depth": p["depth"]}
            for p in pages_data
        ],
        # What the run just cost, so the UI can show the day's remaining
        # allowance without asking for it again.
        "usage": _usage_json(run_usage),
    }
    is_closed = await send_ws_message(websocket, "crawlComplete", json.dumps(crawl_payload), data=crawl_payload, is_closed=is_closed)

    # A crawler that followed every link on a large site can come back with
    # forty pages, most of them a blog post or a login screen the user never
    # wanted rebuilt. One model call per page makes that expensive, so the
    # pages are shown first when the user asked for that.
    if params.confirm_pages and not is_closed and len(crawl_result.pages) > 1:
        chosen = await _await_page_selection(websocket, crawl_result.pages)
        if chosen is None:
            # No answer: the client is gone, or it never understood the
            # question. Generating everything anyway would spend the user's
            # money on a reply they did not give.
            print("[URL2CODE] No page selection received; stopping the run")
            await _stop_run(websocket, is_closed, "No pages chosen - nothing was generated.")
            return
        known = {page.path for page in crawl_result.pages}
        wanted = [path for path in known if path in chosen]
        if not wanted:
            # An empty selection is a decision, not a failure: the user
            # looked at the list and chose none of it.
            print("[URL2CODE] Every page was deselected; nothing to generate")
            await _stop_run(websocket, is_closed, "No pages selected - nothing to generate.")
            return
        dropped = len(crawl_result.pages) - len(wanted)
        if dropped:
            print(f"[URL2CODE] Generating {len(wanted)} of {len(crawl_result.pages)} pages")
        # Everything downstream works from the crawl, so narrowing it here is
        # what the rest of the run sees: media, link maps, the project
        # structure prompt and the portal all agree on the same pages.
        keep = set(wanted)
        crawl_result.pages = [page for page in crawl_result.pages if page.path in keep]
        pages_data = [data for data in pages_data if data["path"] in keep]
        crawl_payload["pages"] = [
            {"path": p["path"], "title": p["title"], "depth": p["depth"]} for p in pages_data
        ]
        crawl_payload["pagesFound"] = len(crawl_result.pages)
        crawl_payload["screenshots"] = {
            path: url for path, url in screenshots.items() if path in keep
        }
        crawl_payload["sourceUrls"] = {
            path: url for path, url in crawl_payload["sourceUrls"].items() if path in keep
        }
        for path in list(run.pages):
            if path not in keep:
                run.pages.pop(path, None)
        try:
            checkpoint("crawled")
        except OSError as exc:
            print(f"[URL2CODE] Could not checkpoint run {run.run_id}: {exc}")
        is_closed = await send_ws_message(
            websocket,
            "status",
            f"Generating {len(wanted)} page{'' if len(wanted) == 1 else 's'}.",
            is_closed=is_closed,
        )

    # A single file is one model call for the whole site, so a large crawl is
    # generated per page instead - a truncated document helps nobody.
    single_prompt = ""
    use_single_file = params.single_file and not is_framework
    if params.single_file and is_framework:
        # A framework project is one component per route; one HTML file
        # cannot hold that.
        is_closed = await send_ws_message(
            websocket,
            "status",
            "Single file is not available for framework projects - "
            "generating one page component per route.",
            is_closed=is_closed,
        )
    if use_single_file:
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

    async def _generate_portal() -> str:
        """Generate the landing/portal page, or "" when none is usable."""
        nonlocal is_closed
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
        attempt = 0
        while not (
            structure_result.text
            and _is_usable_html(_clean_llm_output(structure_result.text))
        ):
            # A plan instead of a page. Ask again before giving up.
            if not retry_budget.take():
                break
            attempt += 1
            print(f"[URL2CODE] Structure answer was not HTML, retry {attempt}...")
            is_closed = await send_ws_message(
                websocket,
                "status",
                f"Retrying generation ({attempt}/{retry_budget.total})...",
                is_closed=is_closed,
            )
            structure_result = await _generate_with_llm(
                project_structure_prompt,
                params,
                websocket,
                is_closed,
                event_prefix=f"structure-retry-{attempt}",
            )

        # The portal is a generated overview, not part of the copy: losing
        # it must not throw away the pages, which are the actual clone.
        portal = (
            _clean_llm_output(structure_result.text) if structure_result.text else ""
        )
        if _is_usable_html(portal):
            return portal
        print(
            "[URL2CODE] No usable landing page "
            f"({structure_result.empty_reason or 'not HTML'}); continuing with pages"
        )
        return ""

    # Fonts, favicon and base colors are site-wide: the home page decides.
    home = next((p for p in crawl_result.pages if p.path == "/"), crawl_result.pages[0])
    site_head = build_head(home.meta, home.design)

    def _finish_page(page_data: Dict[str, Any], text: str) -> str:
        """Turn a model answer into the stored page, or "" when unusable."""
        if is_framework:
            component = clean_component_output(text)
            if not is_usable_component(component):
                return ""
            # Captured head images (favicon) are localized with the page.
            return localize(
                wrap_component_preview(
                    component,
                    params.stack,
                    route_for_path(page_data.get("path", "/")),
                    page_data.get("title", "Page"),
                    site_head,
                )
            )
        cleaned = _clean_llm_output(text)
        return localize(cleaned) if _is_usable_html(cleaned) else ""

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
                    return localize(cleaned)
                commentary = " ".join(cleaned.split())[:160]
                print(f"[URL2CODE] {prefix}: not a complete HTML document: {commentary}")
                return None

            single_html = await attempt_single_file(single_prompt, "single-file")

            # Free models sometimes return an empty body or a plan on the
            # first call; retrying saves the run instead of a hard error.
            attempt = 0
            while not single_html and retry_budget.take():
                attempt += 1
                print(f"[URL2CODE] Unusable single-file response, retry {attempt}...")
                is_closed = await send_ws_message(
                    websocket,
                    "status",
                    f"Retrying generation ({attempt}/{retry_budget.total})...",
                    is_closed=is_closed,
                )
                single_html = await attempt_single_file(
                    single_prompt, f"single-file-retry-{attempt}"
                )

            # A model that ran out of output budget will do so again on an
            # identical prompt; asking for fewer, smaller pages is the only
            # retry with a different outcome. It is still a model call, so it
            # comes out of the same budget.
            if not single_html and last_result.truncated and retry_budget.take():
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
                # Every variant came back unusable, so the run produced no
                # page at all. The unit is returned: the user asked for a
                # clone and received an error message.
                accounts_accounts.refund_action(account, "generate")
                if not is_closed:
                    await websocket.close(APP_ERROR_WEB_SOCKET_CODE)
                return

            all_code = {"/": single_html}
            total_pages_to_gen = 1
            print(f"[URL2CODE] Single-file clone: {len(single_html)} chars")

        else:
            all_code = {}
            # The mock layer goes in first: every page prompt should know
            # which endpoints the site called and what they returned, or the
            # clone will render empty tables and invent products.
            mock_files = clone_mock.build_mock_layer(
                clone_mock.Capture.from_json(crawl_result.api)
            )
            for name, content in mock_files.items():
                all_code[f"{GENERATED_FILE_PREFIX}{name}"] = content
                run.generated_files[name] = content
            if mock_files:
                print(f"[URL2CODE] Mock layer: {len(mock_files)} files", flush=True)

            # The portal is an HTML overview; a framework project routes
            # straight to the crawled pages instead.
            if not is_framework:
                portal = await _generate_portal()
                if portal:
                    all_code["project-structure"] = localize(portal)
                    run.portal = all_code["project-structure"]

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
                    run.database_schema = all_code["database-schema"]

                    # The schema on its own is a document. A server built from
                    # it is what makes the clone run: the pages' forms and
                    # lists have something real to talk to. Generated from the
                    # schema rather than from another model call, so the two
                    # cannot disagree about what the tables are.
                    server_files = clone_backend.build_server(
                        run.database_schema,
                        [page.path for page in crawl_result.pages],
                        _site_name_for(crawl_result),
                    )
                    for name, content in server_files.items():
                        all_code[f"{GENERATED_FILE_PREFIX}{name}"] = content
                    if server_files:
                        for name, content in server_files.items():
                            run.generated_files[name] = content
                        is_closed = await send_ws_message(
                            websocket,
                            "status",
                            f"Built a runnable server for {len(server_files)} files...",
                            is_closed=is_closed,
                        )

            total_pages_to_gen = len(pages_data)
            is_closed = await send_ws_message(
                websocket,
                "status",
                clone_runner.generating_status(total_pages_to_gen, PAGE_CONCURRENCY),
                is_closed=is_closed,
            )

            # Pages are independent calls, so waiting for each one in turn
            # made a ten-page site a twenty-minute run. The limit keeps free
            # tiers from answering every request with a rate-limit error.
            page_slots = asyncio.Semaphore(PAGE_CONCURRENCY)

            async def generate_page(index: int, page_data: Dict[str, Any]):
                async with page_slots:
                    while True:
                        result = await _run_agent_for_page(
                            page_data,
                            params,
                            websocket,
                            index,
                            total_pages_to_gen,
                            link_map=link_map,
                        )
                        if result.text and _finish_page(page_data, result.text):
                            break
                        # Pages share one allowance, so a run where every page
                        # came back unusable stops after `max retries` extra
                        # calls in total, not that many per page.
                        if not retry_budget.take():
                            break
                        print(
                            f"[URL2CODE] {page_data.get('path', '/')}: unusable "
                            f"answer, retry {retry_budget.used}/{retry_budget.total}"
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

                    cleaned_page = (
                        _finish_page(pages_data[index], result.text) if result.text else ""
                    )
                    if result.text and not cleaned_page:
                        # Commentary or a half-written file; report the page as
                        # failed rather than saving a file that will not render.
                        print(f"[URL2CODE] {page_path}: answer was not a complete page")

                    if cleaned_page:
                        all_code[page_path] = cleaned_page
                        page_record = run.page(page_path)
                        page_record.status = clone_runs.COMPLETE
                        page_record.code = cleaned_page
                        page_record.error = ""
                        page_record.attempts += 1
                        try:
                            checkpoint()
                        except OSError as exc:
                            print(f"[URL2CODE] Checkpoint failed for {page_path}: {exc}")
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
                        page_record = run.page(page_path)
                        page_record.status = clone_runs.FAILED
                        page_record.error = result.empty_reason or "no usable answer"
                        page_record.attempts += 1
                        try:
                            checkpoint()
                        except OSError as exc:
                            print(f"[URL2CODE] Checkpoint failed for {page_path}: {exc}")
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
        # A provider that is down is not something the user chose, and the
        # pages already fetched cannot stand in for the clone.
        accounts_accounts.refund_action(account, "generate")
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

    # A framework project has no portal; a copy of a page would become a
    # duplicate route.
    if not is_framework and not all_code.get("project-structure") and generated_pages:
        fallback = generated_pages[0]
        print(f"[URL2CODE] Using first page '{fallback}' as main code")
        all_code["project-structure"] = all_code[fallback]

    # Every page failed. Say so instead of shipping an empty payload the
    # frontend would have to guess about.
    if not any(all_code.values()):
        accounts_accounts.refund_action(account, "generate")
        is_closed = await send_ws_message(
            websocket,
            "error",
            "Every page failed to generate. The model may be rate limited or "
            "returning empty responses вЂ” retry or pick a different model.",
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
            "runId": run.run_id,
        }
        # Persist the finished run before announcing it, so a client that
        # reconnects immediately can already load it.
        try:
            run.phase = "done"
            clone_runs.save_run(run)
        except OSError as exc:
            print(f"[URL2CODE] Could not save final run state: {exc}")

        # The clone is on disk and paid for, so it becomes one of the
        # account's projects without being asked to. A finished clone the
        # user has to remember to keep is a clone that is lost.
        project_usage = accounts_accounts.note_project(
            account,
            run.run_id,
            _site_name_for(crawl_result),
            params.url,
        )
        final_payload["usage"] = _usage_json(project_usage)

        # Told once, here, where a clone actually finishes. Announced from
        # anywhere else it would either go missing or fire for runs that
        # failed. Returns whether it was sent; nobody waits on it, and a
        # failure to deliver must not cost the person their finished site.
        await announce_ready(account, _site_name_for(crawl_result))

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
