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

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

import httpx

from config import OPENAI_BASE_URL, OPENAI_MODEL

# A single answer has to carry a whole HTML document, so the ceiling is high;
# beyond this, models that support it gain little and models that do not
# reject the request outright.
MAX_OUTPUT_TOKENS = 16_000

# One number for the whole path: the HTTP client and any outer wait must agree,
# or the shorter of the two silently defines the real limit.
REQUEST_TIMEOUT_SECONDS = 300

# Defaults for providers whose model the UI does not choose. Overridable
# through the environment (see config) or by passing an explicit model.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
# OpenRouter retires free tiers without removing the ":free" entry from its
# catalog, so a stale default fails every request with a 404 that looks like a
# generic model error. Verify this id still answers before changing it.
DEFAULT_OPENROUTER_MODEL = "inclusionai/ling-3.0-flash-fin:free"

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

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "LlmConfig":
        data = raw or {}
        return cls(
            provider=str(data.get("provider") or "none"),
            model=str(data.get("model") or ""),
            api_key=str(data.get("api_key") or ""),
            base_url=str(data.get("base_url") or ""),
        )

    @property
    def is_usable(self) -> bool:
        if self.provider in ("", "none"):
            return False
        # A local OpenAI-compatible server (Ollama, vLLM) needs no key.
        return bool(self.api_key) or self.provider == "custom"


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
    if not isinstance(choices, list) or not choices:
        return Completion(empty_reason=f"{provider} returned no choices")

    choice = cast(Dict[str, Any], choices[0]) if isinstance(choices[0], dict) else {}
    message = cast(Dict[str, Any], choice.get("message") or {})
    content = join_content(message.get("content")).strip()
    finish_reason = str(
        choice.get("finish_reason") or choice.get("native_finish_reason") or ""
    )

    if content:
        return Completion(text=content, truncated=finish_reason == "length")

    # Reasoning models sometimes emit the document inside the reasoning
    # channel and leave content empty; the run is recoverable if it is there.
    reasoning = join_content(
        message.get("reasoning") or message.get("reasoning_content")
    ).strip()
    salvaged = salvage_html(reasoning)
    if salvaged:
        return Completion(text=salvaged, truncated=finish_reason == "length")

    if finish_reason == "length":
        return Completion(
            empty_reason=(
                f"{provider} hit the output token limit before writing any HTML"
                + (" (it spent the budget on reasoning)" if reasoning else "")
            ),
            truncated=True,
        )
    if reasoning:
        return Completion(empty_reason=f"{provider} returned reasoning but no answer")
    return Completion(empty_reason=f"{provider} returned an empty message")


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
    client: httpx.AsyncClient, cfg: LlmConfig, messages: List[Dict[str, str]], max_tokens: int
) -> Completion:
    label = _provider_label(cfg.provider)
    headers = {"Content-Type": "application/json"}
    if cfg.api_key and cfg.api_key != "no-key":
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    response = await client.post(
        _openai_style_url(cfg),
        headers=headers,
        json={
            "model": _openai_style_model(cfg),
            "messages": messages,
            "max_tokens": max_tokens,
        },
    )
    if response.status_code != 200:
        raise ProviderError(describe_http_error(label, response))
    return extract_chat_completion(label, response.json())


async def _complete_anthropic(
    client: httpx.AsyncClient, cfg: LlmConfig, messages: List[Dict[str, str]], max_tokens: int
) -> Completion:
    system_msg = ""
    user_messages: List[Dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
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
    blocks: List[Any] = list(data.get("content") or [])
    text = "".join(
        str(cast(Dict[str, Any], block).get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    truncated = data.get("stop_reason") == "max_tokens"
    if text:
        return Completion(text=text, truncated=truncated)
    if truncated:
        return Completion(
            empty_reason="Anthropic hit the output token limit before writing any HTML",
            truncated=True,
        )
    return Completion(empty_reason="Anthropic returned an empty message")


async def _complete_gemini(
    client: httpx.AsyncClient, cfg: LlmConfig, messages: List[Dict[str, str]], max_tokens: int
) -> Completion:
    contents: List[Dict[str, Any]] = [
        {"role": "user", "parts": [{"text": msg["content"]}]} for msg in messages
    ]
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
            )
        )

    candidate = cast(Dict[str, Any], candidates[0])
    content = cast(Dict[str, Any], candidate.get("content") or {})
    parts: List[Any] = list(content.get("parts") or [])
    text = "".join(
        str(cast(Dict[str, Any], part).get("text") or "")
        for part in parts
        if isinstance(part, dict)
    ).strip()
    finish = str(candidate.get("finishReason") or "")
    if text:
        return Completion(text=text, truncated=finish == "MAX_TOKENS")
    if finish == "MAX_TOKENS":
        return Completion(
            empty_reason="Gemini hit the output token limit before writing any HTML",
            truncated=True,
        )
    return Completion(
        empty_reason=f"Gemini returned an empty message ({finish or 'no reason given'})"
    )


async def complete(
    cfg: LlmConfig,
    system: str,
    user: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Completion:
    """Ask one provider one question.

    Raises ProviderError when the provider refuses (bad key, retired model,
    rate limit); returns a Completion with `empty_reason` when it answers but
    says nothing usable.
    """
    if not cfg.is_usable:
        return Completion(empty_reason="no provider configured")

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    async with httpx.AsyncClient(timeout=timeout) as client:
        if cfg.provider == "anthropic":
            return await _complete_anthropic(client, cfg, messages, max_tokens)
        if cfg.provider == "gemini":
            return await _complete_gemini(client, cfg, messages, max_tokens)
        if cfg.provider in OPENAI_STYLE_PROVIDERS:
            if cfg.provider == "custom" and not cfg.base_url:
                return Completion(empty_reason="custom provider has no base URL")
            return await _complete_openai_style(client, cfg, messages, max_tokens)

    return Completion(empty_reason=f"unknown provider '{cfg.provider}'")


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
