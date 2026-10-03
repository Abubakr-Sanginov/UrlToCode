"""One HTTP client for the plain chat-completion providers.

Two callers need to ask a model a single question over HTTP: the url-to-code
route (generate this page) and the Playwright crawler (what should I click on
this page?). Each used to carry its own copy of the request bodies, the error
handling and the answer parsing, so a fix in one — reading `content` that
arrives as a list of parts, spotting an error object inside an HTTP 200 —
never reached the other.

The agent providers in `agent/providers/` stay separate on purpose: they model
tool-calling sessions with streaming and state. This module is the one-shot
case, and nothing here knows about websockets, prompts or crawling.
"""

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

import httpx

from config import OPENAI_BASE_URL, OPENAI_MODEL

# A single answer has to carry a whole HTML document, so the ceiling is high;
# beyond this, models that support it gain little and models that do not
# reject the request outright.
MAX_OUTPUT_TOKENS = 16_000

# One number for the whole path: the HTTP client and any outer wait must agree,
# or the shorter of the two silently defines the real limit.
#
# Measured, not guessed. A reasoning model spends most of its output budget
# thinking before it writes the answer: the one this project was tested
# against took 201s, 269s and 303s for the same 533-token prompt, and a page
# needs several calls, which is where "ten minutes for one page" comes from.
# At 300s the limit sat exactly on top of the model's own pace, so runs
# failed on the calls that happened to be a little slow - a failure with no
# cause in the code under test. Ten minutes leaves room for a slow one.
REQUEST_TIMEOUT_SECONDS = 600

# A gateway that is briefly unavailable is worth a second go: one page
# costs minutes, and a 502 partway through used to throw all of it away.
# One retry, not a loop - a provider that is genuinely down must fail fast
# enough that the user is not left waiting on it.
TRANSIENT_ATTEMPTS = 1
_TRANSIENT_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# Long enough for a struggling gateway to come back, short enough that a
# real failure is still reported while the user is watching.
RETRY_PAUSE_SECONDS = 5

# Defaults for providers whose model the UI does not choose. Overridable
# through the environment (see config) or by passing an explicit model.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
# OpenRouter retired the free tier of this entry from its catalog, so the
# ":free" slug now answers 404 with "this model is unavailable for free"
# while the paid slug of the same model works. Verified against the gateway
# before changing it.
DEFAULT_OPENROUTER_MODEL = "inclusionai/ling-3.0-flash-fin"

# Set to low/medium/high to tell a reasoning model how hard to think. Left
# empty the provider decides, which is right for an ordinary model and wrong
# for a reasoning one asked for a page: it spends the entire output budget
# thinking, answers nothing, and takes past a gateway's time limit doing it.
DEFAULT_REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "").strip().lower()

OPENAI_STYLE_PROVIDERS = frozenset({"openrouter", "openai", "custom"})


class ProviderError(Exception):
    """A provider rejected the request and said why.

    Carries a message safe to show the user, so a failed run reports the real
    cause (retired model, bad key, rate limit) instead of a generic
    "generation failed".
    """


@dataclass
class LlmConfig:
    """Everything needed to reach one provider."""

    provider: str = "none"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    # How hard a reasoning model should think before it answers: "", "low",
    # "medium" or "high". Empty leaves it to the provider, which is right
    # for a normal model and wrong for a reasoning one: asked to write a
    # page, the same model spent 259s and its whole output budget thinking
    # and returned nothing, while `low` returned the page in 34s.
    reasoning_effort: str = ""

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "LlmConfig":
        data = raw or {}
        return cls(
            provider=str(data.get("provider") or "none"),
            model=str(data.get("model") or ""),
            api_key=str(data.get("api_key") or ""),
            base_url=str(data.get("base_url") or ""),
            reasoning_effort=str(data.get("reasoning_effort") or ""),
        )

    @property
    def is_usable(self) -> bool:
        if self.provider in ("", "none"):
            return False
        # A local OpenAI-compatible server (Ollama, vLLM) needs no key.
        return bool(self.api_key) or self.provider == "custom"


@dataclass
class Image:
    """One picture to show the model, as the bytes it arrived in.

    Kept as raw bytes plus a mime type rather than a data URL, because each
    provider spells the same picture differently and only one of those
    spellings should be written in this file.
    """

    data: bytes
    mime_type: str = "image/png"

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("An image with no bytes is not an image.")

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass
class Completion:
    """What came back, and — when nothing did — why.

    `empty_reason` exists so callers can tell the user what actually happened
    (token limit, reasoning-only answer, provider hiccup) instead of "the
    model returned nothing".
    """

    text: Optional[str] = None
    empty_reason: str = ""
    truncated: bool = False
    # What the provider says it was charged for, when it says. `None` means
    # the provider did not report it, which is not the same as zero: an
    # unpriced call still costs the user money.
    usage: Optional[Dict[str, Any]] = None

    def __bool__(self) -> bool:
        return bool(self.text)


def describe_http_error(provider: str, response: httpx.Response) -> str:
    """Extract the provider's own explanation from an error response."""
    detail = ""
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        body = cast(Dict[str, Any], payload)
        error: Any = body.get("error")
        if isinstance(error, dict):
            detail = str(cast(Dict[str, Any], error).get("message") or "")
        elif isinstance(error, str):
            detail = error
        if not detail:
            detail = str(body.get("message") or "")

    if not detail:
        detail = response.text[:300].strip()

    hint = ""
    if response.status_code in (401, 403):
        hint = " Check that the API key is valid."
    elif response.status_code == 404:
        hint = " Pick a different model in Settings."
    elif response.status_code == 429:
        hint = " Wait and retry, or pick a different model."

    return f"{provider} returned {response.status_code}: {detail}{hint}"


def join_content(raw: Any) -> str:
    """Flatten the `content` field of an OpenAI-style message.

    Several OpenAI-compatible gateways return content as a list of typed
    parts rather than a string; treating that list as empty is what turned a
    perfectly good answer into "the model returned an empty clone".
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: List[str] = []
        for item in cast(List[Any], raw):
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = cast(Dict[str, Any], item).get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def salvage_html(text: str) -> str:
    """Return the HTML document inside `text`, or "" if there is none."""
    match = re.search(r"<!DOCTYPE html.*|<html[\s>].*", text, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""


def extract_chat_completion(provider: str, data: Dict[str, Any]) -> Completion:
    """Pull the answer out of an OpenAI-style 200 response.

    Raises ProviderError when the body carries an error object — OpenRouter
    reports rate limits and upstream failures that way, with HTTP 200, so
    those used to surface as a mysterious empty generation.
    """
    error: Any = data.get("error")
    if isinstance(error, dict):
        detail = str(cast(Dict[str, Any], error).get("message") or error)
        raise ProviderError(f"{provider} returned an error: {detail}")
    if isinstance(error, str) and error:
        raise ProviderError(f"{provider} returned an error: {error}")

    choices: Any = data.get("choices")
    usage: Optional[Dict[str, Any]] = None
    raw_usage: Any = data.get("usage")
    if isinstance(raw_usage, dict):
        usage = cast(Dict[str, Any], raw_usage)
    if not isinstance(choices, list) or not choices:
        return Completion(empty_reason=f"{provider} returned no choices", usage=usage)

    choice = cast(Dict[str, Any], choices[0]) if isinstance(choices[0], dict) else {}
    message = cast(Dict[str, Any], choice.get("message") or {})
    content = join_content(message.get("content")).strip()
    finish_reason = str(
        choice.get("finish_reason") or choice.get("native_finish_reason") or ""
    )

    if content:
        return Completion(text=content, truncated=finish_reason == "length", usage=usage)

    # Reasoning models sometimes emit the document inside the reasoning
    # channel and leave content empty; the run is recoverable if it is there.
    reasoning = join_content(
        message.get("reasoning") or message.get("reasoning_content")
    ).strip()
    salvaged = salvage_html(reasoning)
    if salvaged:
        return Completion(text=salvaged, truncated=finish_reason == "length", usage=usage)

    if finish_reason == "length":
        return Completion(
            empty_reason=(
                f"{provider} hit the output token limit before writing any HTML"
                + (" (it spent the budget on reasoning)" if reasoning else "")
            ),
            truncated=True,
            usage=usage,
        )
    if reasoning:
        return Completion(
            empty_reason=f"{provider} returned reasoning but no answer", usage=usage
        )
    return Completion(empty_reason=f"{provider} returned an empty message", usage=usage)


def _openai_style_url(cfg: LlmConfig) -> str:
    if cfg.provider == "openrouter":
        return "https://openrouter.ai/api/v1/chat/completions"
    if cfg.provider == "custom":
        return cfg.base_url.rstrip("/") + "/chat/completions"
    base = (cfg.base_url or OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
    return f"{base}/chat/completions"


def _openai_style_model(cfg: LlmConfig) -> str:
    if cfg.model:
        return cfg.model
    if cfg.provider == "openrouter":
        return DEFAULT_OPENROUTER_MODEL
    if cfg.provider == "openai":
        return OPENAI_MODEL
    return "default"


def _provider_label(provider: str) -> str:
    return {
        "openrouter": "OpenRouter",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Gemini",
        "custom": "Custom provider",
    }.get(provider, provider or "Provider")


async def _complete_openai_style(
    client: httpx.AsyncClient,
    cfg: LlmConfig,
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> Completion:
    label = _provider_label(cfg.provider)
    headers = {"Content-Type": "application/json"}
    if cfg.api_key and cfg.api_key != "no-key":
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    body: Dict[str, Any] = {
        "model": _openai_style_model(cfg),
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if cfg.reasoning_effort:
        # OpenRouter's shape. Providers that do not know the field ignore
        # it, and the one that rejected a stronger version of the same idea
        # answered with a clear message rather than a 502.
        body["reasoning"] = {"effort": cfg.reasoning_effort}

    response = await client.post(
        _openai_style_url(cfg),
        headers=headers,
        json=body,
    )
    if response.status_code != 200:
        raise ProviderError(describe_http_error(label, response))
    return extract_chat_completion(label, response.json())


async def _complete_anthropic(
    client: httpx.AsyncClient,
    cfg: LlmConfig,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    images: Optional[List[Image]] = None,
) -> Completion:
    system_msg = ""
    user_messages: List[Dict[str, Any]] = []
    images = list(images or [])
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            # Anthropic spells an image as a source block, and the text has
            # to be a block too once any image is present.
            if images:
                image_blocks: List[Dict[str, Any]] = [
                    {"type": "text", "text": msg["content"]}
                ]
                for image in images:
                    image_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image.mime_type,
                                "data": base64.b64encode(image.data).decode("ascii"),
                            },
                        }
                    )
                user_messages.append({"role": "user", "content": image_blocks})
                images = []
            else:
                user_messages.append(msg)

    response = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": cfg.model or DEFAULT_ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system_msg,
            "messages": user_messages,
        },
    )
    if response.status_code != 200:
        raise ProviderError(describe_http_error("Anthropic", response))

    data: Dict[str, Any] = response.json()
    raw_usage: Any = data.get("usage")
    usage: Optional[Dict[str, Any]] = (
        cast(Dict[str, Any], raw_usage) if isinstance(raw_usage, dict) else None
    )
    blocks: List[Any] = list(data.get("content") or [])
    text = "".join(
        str(cast(Dict[str, Any], block).get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    truncated = data.get("stop_reason") == "max_tokens"
    if text:
        return Completion(text=text, truncated=truncated, usage=usage)
    if truncated:
        return Completion(
            empty_reason="Anthropic hit the output token limit before writing any HTML",
            truncated=True,
            usage=usage,
        )
    return Completion(empty_reason="Anthropic returned an empty message", usage=usage)


async def _complete_gemini(
    client: httpx.AsyncClient,
    cfg: LlmConfig,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    images: Optional[List[Image]] = None,
) -> Completion:
    # Gemini takes one `contents` list, with the system prompt folded in as
    # a system-role turn, and spells an image as inline_data rather than a
    # URL.
    pictures = list(images or [])
    contents: List[Dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "system":
            contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            continue
        parts: List[Dict[str, Any]] = [{"text": msg["content"]}]
        for picture in pictures:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": picture.mime_type,
                        "data": base64.b64encode(picture.data).decode("ascii"),
                    }
                }
            )
        pictures = []
        contents.append({"role": "user", "parts": parts})
    model = cfg.model or DEFAULT_GEMINI_MODEL

    response = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": cfg.api_key},
        json={
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
    )
    if response.status_code != 200:
        raise ProviderError(describe_http_error("Gemini", response))

    data: Dict[str, Any] = response.json()
    candidates: List[Any] = list(data.get("candidates") or [])
    if not candidates:
        block = cast(Dict[str, Any], data.get("promptFeedback") or {}).get("blockReason")
        return Completion(
            empty_reason=(
                f"Gemini blocked the prompt ({block})" if block
                else "Gemini returned no candidates"
            ),
            usage=_gemini_usage(data),
        )

    candidate = cast(Dict[str, Any], candidates[0])
    content = cast(Dict[str, Any], candidate.get("content") or {})
    answer_parts: List[Any] = list(content.get("parts") or [])
    text = "".join(
        str(cast(Dict[str, Any], part).get("text") or "")
        for part in answer_parts
        if isinstance(part, dict)
    ).strip()
    finish = str(candidate.get("finishReason") or "")
    if text:
        return Completion(text=text, truncated=finish == "MAX_TOKENS", usage=_gemini_usage(data))
    if finish == "MAX_TOKENS":
        return Completion(
            empty_reason="Gemini hit the output token limit before writing any HTML",
            truncated=True,
            usage=_gemini_usage(data),
        )
    return Completion(
        empty_reason=f"Gemini returned an empty message ({finish or 'no reason given'})",
        usage=_gemini_usage(data),
    )


def _gemini_usage(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Gemini counts tokens under its own names; the rest of the code does not."""
    raw: Any = data.get("usageMetadata")
    if not isinstance(raw, dict):
        return None
    body = cast(Dict[str, Any], raw)
    return {
        "prompt_tokens": body.get("promptTokenCount"),
        "completion_tokens": body.get("candidatesTokenCount"),
    }


async def complete(
    cfg: LlmConfig,
    system: str,
    user: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    images: Optional[List[Image]] = None,
    attempt: int = 0,
) -> Completion:
    """Ask one provider one question, optionally showing it some pictures.

    `images` are attached to the user message. Passing one to a model that
    cannot see is the caller's mistake to make, not an error here: what
    happens is the provider's business, and this layer's job is to spell
    the request the way each provider expects.

    Raises ProviderError when the provider refuses (bad key, retired model,
    rate limit); returns a Completion with `empty_reason` when it answers
    but says nothing usable.

    One retry for a provider that is briefly down. A page costs several
    minutes of generation, and a gateway returning 502 partway through - a
    blip, not a refusal - used to throw the whole of that away. A second
    attempt costs the same wait and usually succeeds, which is the
    difference between a pause and a lost run.
    """
    if not cfg.is_usable:
        return Completion(empty_reason="no provider configured")

    if images:
        # One user turn holding the text and the pictures side by side. A
        # picture in a turn of its own is read as a separate exchange by
        # some providers, and the model then answers the wrong message.
        content: List[Dict[str, Any]] = [{"type": "text", "text": user}]
        for image in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image.data_url()},
                }
            )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if cfg.provider == "anthropic":
                return await _complete_anthropic(client, cfg, messages, max_tokens, images)
            if cfg.provider == "gemini":
                return await _complete_gemini(client, cfg, messages, max_tokens, images)
            if cfg.provider in OPENAI_STYLE_PROVIDERS:
                if cfg.provider == "custom" and not cfg.base_url:
                    return Completion(empty_reason="custom provider has no base URL")
                return await _complete_openai_style(client, cfg, messages, max_tokens)
    except httpx.HTTPError as exc:
        # httpx exceptions carry no message of their own: str() of a
        # ReadTimeout is "". Handed straight to the user that is a blank
        # error, which is the one reply that cannot be acted on - they
        # cannot tell a timeout from a bad key from a dropped connection.
        if _is_transient(exc) and attempt < TRANSIENT_ATTEMPTS:
            await asyncio.sleep(RETRY_PAUSE_SECONDS)
            return await complete(
                cfg, system, user, max_tokens, timeout, images, attempt + 1
            )
        raise ProviderError(_network_reason(exc, cfg.model)) from exc

    return Completion(empty_reason=f"unknown provider '{cfg.provider}'")


def _is_transient(exc: httpx.HTTPError) -> bool:
    """Is this the provider wobbling rather than refusing?

    TransportError is the base of every connection problem - a timeout, a
    refused connection, a response that stopped halfway. All of them are
    worth another go, and all of them happen here: a slow reasoning model
    holds the connection open for minutes, and a gateway that drops one
    mid-answer is having a moment rather than refusing. An authentication
    failure or a retired model is not worth retrying, and retrying it only
    spends the user's time to reach the same refusal.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUSES
    return False


def _network_reason(exc: httpx.HTTPError, model: str) -> str:
    """A sentence saying what went wrong between here and the provider.

    Each case names the thing the user can act on. A timeout on a large
    page prompt is a different problem from an endpoint that will not
    answer at all, and they look identical if you only say "error".
    """
    name = model or "the model"
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"{name} did not answer in time. A page with a lot of markup can "
            "run past the limit; a longer wait or a smaller page will get past it."
        )
    if isinstance(exc, httpx.ConnectError):
        return f"Could not reach the provider ({exc.request.url.host})."
    if isinstance(exc, httpx.RemoteProtocolError):
        return f"The provider closed the connection before answering ({exc.request.url.host})."
    if isinstance(exc, httpx.ReadError):
        # The connection died partway through the answer. Saying so is the
        # difference between "the provider is flaky" and a bare ReadError.
        return (
            f"The connection to the provider dropped partway through the answer "
            f"({exc.request.url.host}). The generation is long enough for this."
        )
    return f"The request to the provider failed: {type(exc).__name__}."


def read_worker_params(argv: List[str]) -> Dict[str, Any]:
    """Read crawler parameters from stdin, or from argv as a fallback.

    Parameters carry an API key, and a command line is world-readable through
    the process list, so the key is piped in instead.
    """
    import sys

    if len(argv) > 1 and argv[1] != "-":
        return cast(Dict[str, Any], json.loads(argv[1]))
    return cast(Dict[str, Any], json.loads(sys.stdin.read()))


__all__ = [
    "Completion",
    "Image",
    "LlmConfig",
    "MAX_OUTPUT_TOKENS",
    "ProviderError",
    "REQUEST_TIMEOUT_SECONDS",
    "complete",
    "describe_http_error",
    "extract_chat_completion",
    "join_content",
    "read_worker_params",
    "salvage_html",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OPENROUTER_MODEL",
]
