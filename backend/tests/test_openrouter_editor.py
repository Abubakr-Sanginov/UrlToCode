"""The editor must be able to reach OpenRouter.

The clone path always could; the editor could not. The agent spoke the
OpenAI Responses API, and OpenRouter serves only /chat/completions, so a
project configured for OpenRouter could clone a site and then not edit it -
the edit went to whichever of OpenAI/Gemini/Anthropic had a key in .env, and
with a stale one it failed with a 401 the user never saw explained.

These tests pin the routing: which provider a request goes to, and what
the session actually puts on the wire.
"""

import json
from typing import Any, Dict, List

import httpx
import pytest

from agent.providers.factory import create_provider_session
from agent.providers.openrouter import OpenRouterProviderSession
from llm import (
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS,
    Llm,
    get_openrouter_api_name,
)
from routes.model_choice_sets import OPENROUTER_MODELS as OPENROUTER_EDIT_MODELS
from routes.generate_code import ModelSelectionStage

Messages = List[Dict[str, Any]]


# --- which model a request goes to -------------------------------------------


async def _select(**keys: str) -> List[Llm]:
    stage = ModelSelectionStage(_noop)
    return await stage.select_models(
        generation_type="update",
        input_mode="image",
        openai_api_key=keys.get("openai"),
        anthropic_api_key=keys.get("anthropic"),
        gemini_api_key=keys.get("gemini"),
        openrouter_api_key=keys.get("openrouter"),
    )


async def _noop(_message: str) -> None:
    return None


@pytest.mark.asyncio
async def test_openrouter_wins_over_a_stale_gemini_key() -> None:
    """The failure this fixes: a dead Gemini key took the edit with it."""
    models = await _select(openrouter="key", gemini="stale")

    assert models == list(OPENROUTER_EDIT_MODELS)


@pytest.mark.asyncio
async def test_openrouter_is_used_when_it_is_the_only_key() -> None:
    models = await _select(openrouter="key")

    assert models
    assert all(model in OPENROUTER_MODELS for model in models)


@pytest.mark.asyncio
async def test_the_two_variants_are_not_the_same_model() -> None:
    # An edit shows the user two answers; two copies of one answer is one
    # answer drawn twice, and doubles the cost for nothing.
    models = await _select(openrouter="key")

    assert len(set(models)) == len(models)


@pytest.mark.asyncio
async def test_without_openrouter_the_other_providers_still_apply() -> None:
    models = await _select(gemini="key")

    assert models
    assert not any(model in OPENROUTER_MODELS for model in models)


# --- what the session sends ---------------------------------------------------


def _session(reasoning_effort: str | None = "low") -> OpenRouterProviderSession:
    """A session with an explicit effort, so "no effort" can be asked for."""
    return OpenRouterProviderSession(
        api_key="key",
        model=Llm.OPENROUTER_SPACE_BUNNY_LOW,
        prompt_messages=[
            {"role": "system", "content": "you build web pages"},
            {"role": "user", "content": "make me a page"},
        ],
        tools=[],
        reasoning_effort=reasoning_effort,
    )


def test_the_session_is_the_openrouter_one_not_the_openai_one() -> None:
    # Reusing the OpenAI provider would post to /responses, which
    # OpenRouter does not serve.
    assert isinstance(_session(), OpenRouterProviderSession)


def test_the_request_goes_to_chat_completions() -> None:
    assert OPENROUTER_BASE_URL.endswith("/api/v1")

    body = _session()._body(stream=True)
    assert body["model"] == "stealth/space-bunny-alpha"
    assert body["stream"] is True


def test_the_system_prompt_is_sent_where_chat_completions_expects_it() -> None:
    body = _session()._body(stream=True)

    assert body["messages"][0] == {
        "role": "system",
        "content": "you build web pages",
    }
    assert body["messages"][1]["content"] == "make me a page"


def test_the_reasoning_effort_is_sent() -> None:
    # Without it this model spends its whole output budget reasoning and
    # returns no code at all - the same trap the clone path had.
    assert _session()._body(stream=True)["reasoning"] == {"effort": "low"}


def test_no_reasoning_block_when_no_effort_was_chosen() -> None:
    assert "reasoning" not in _session(None)._body(stream=True)


def test_the_session_refuses_to_start_without_a_key() -> None:
    with pytest.raises(Exception, match="OpenRouter API key is missing"):
        create_provider_session(
            model=Llm.OPENROUTER_SPACE_BUNNY_LOW,
            prompt_messages=[{"role": "user", "content": "hi"}],
            should_generate_images=False,
            openai_api_key=None,
            openai_base_url=None,
            anthropic_api_key=None,
            gemini_api_key=None,
            replicate_api_key=None,
            openrouter_api_key=None,
        )


def test_every_openrouter_model_maps_to_a_slug_openrouter_knows() -> None:
    # A label with no slug behind it would 404 at request time.
    for model in OPENROUTER_MODELS:
        assert "/" in get_openrouter_api_name(model)


# --- what a streamed turn becomes --------------------------------------------


def _stream(*lines: str) -> List[str]:
    return [f"data: {line}" for line in lines]


TEXT_CHUNKS = _stream(
    json.dumps({"choices": [{"delta": {"content": "<html>"}}]}),
    json.dumps({"choices": [{"delta": {"content": "hi</html>"}}]}),
    json.dumps(
        {
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    ),
    "[DONE]",
)


def test_streamed_text_is_reassembled() -> None:
    import asyncio

    session = _session()
    captured: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        body = "\n".join(TEXT_CHUNKS)
        return httpx.Response(200, content=body.encode())

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    httpx.AsyncClient = patched  # type: ignore[assignment]
    try:
        events: List[Any] = []

        async def sink(event: Any) -> None:
            events.append(event)

        turn = asyncio.run(session.stream_turn(sink))
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert turn.assistant_text == "<html>hi</html>"
    assert any(event.type == "assistant_delta" for event in events)
    assert captured[0]["model"] == "stealth/space-bunny-alpha"
    assert captured[0]["stream"] is True


def test_streamed_tool_call_arguments_are_joined_and_parsed() -> None:
    import asyncio

    session = _session()
    chunks = _stream(
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": '{"path":',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ' "index.html"}'}}
                            ]
                        }
                    }
                ]
            }
        ),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="\n".join(chunks).encode())

    original = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    httpx.AsyncClient = patched  # type: ignore[assignment]
    try:

        async def sink(_event: Any) -> None:
            return None

        turn = asyncio.run(session.stream_turn(sink))
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "write_file"
    # Two fragments, one argument: a half-parsed tool call would run a tool
    # with no path.
    assert turn.tool_calls[0].arguments == {"path": "index.html"}


def test_an_error_chunk_is_raised_not_swallowed() -> None:
    import asyncio

    session = _session()
    chunks = _stream(json.dumps({"error": {"message": "no credit"}}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="\n".join(chunks).encode())

    original = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    httpx.AsyncClient = patched  # type: ignore[assignment]
    try:

        async def sink(_event: Any) -> None:
            return None

        with pytest.raises(RuntimeError, match="no credit"):
            asyncio.run(session.stream_turn(sink))
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]
