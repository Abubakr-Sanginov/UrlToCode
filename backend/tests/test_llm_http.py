"""The shared one-shot chat client: what it accepts, and what it refuses."""

from typing import Any, Dict

import pytest

import llm_http
from llm_http import Completion, LlmConfig, ProviderError


def test_list_content_from_a_gateway_is_not_treated_as_empty() -> None:
    """Some OpenAI-compatible gateways return content as typed parts.

    Reading only string content turned a complete answer into "the model
    returned an empty clone".
    """
    data: Dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "<!DOCTYPE html>"},
                        {"type": "text", "text": "<html></html>"},
                    ]
                },
                "finish_reason": "stop",
            }
        ]
    }

    result = llm_http.extract_chat_completion("OpenRouter", data)

    assert result.text == "<!DOCTYPE html><html></html>"
    assert not result.truncated


def test_error_inside_a_200_body_is_raised_as_a_provider_error() -> None:
    """OpenRouter reports rate limits with HTTP 200 and an error object."""
    data: Dict[str, Any] = {"error": {"code": 429, "message": "Rate limit exceeded"}}

    with pytest.raises(ProviderError) as excinfo:
        llm_http.extract_chat_completion("OpenRouter", data)

    assert "Rate limit exceeded" in str(excinfo.value)


def test_html_is_recovered_from_a_reasoning_only_answer() -> None:
    data: Dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning": "Let me write it.\n<!DOCTYPE html><html>ok</html>",
                },
                "finish_reason": "stop",
            }
        ]
    }

    assert (
        llm_http.extract_chat_completion("OpenRouter", data).text
        == "<!DOCTYPE html><html>ok</html>"
    )


def test_output_token_limit_is_reported_as_the_empty_reason() -> None:
    data: Dict[str, Any] = {
        "choices": [
            {"message": {"content": "", "reasoning": "thinking..."}, "finish_reason": "length"}
        ]
    }

    result = llm_http.extract_chat_completion("OpenRouter", data)

    assert result.text is None
    assert "output token limit" in result.empty_reason
    assert result.truncated


def test_truncation_is_recorded_even_when_content_arrives() -> None:
    """A half-written document must not pass as a finished one."""
    data: Dict[str, Any] = {
        "choices": [
            {"message": {"content": "<!DOCTYPE html><html>"}, "finish_reason": "length"}
        ]
    }

    result = llm_http.extract_chat_completion("OpenRouter", data)

    assert result.text and result.truncated


def test_an_unconfigured_provider_answers_without_a_request() -> None:
    """Never send a request that cannot carry credentials."""
    result = Completion()
    assert not result

    assert not LlmConfig().is_usable
    assert not LlmConfig(provider="openai").is_usable
    # A local OpenAI-compatible server needs no key.
    assert LlmConfig(provider="custom", base_url="http://localhost:11434/v1").is_usable


def test_each_provider_gets_its_own_model_default() -> None:
    """A hard-coded id used to override whatever Settings had chosen."""
    assert (
        llm_http._openai_style_model(LlmConfig(provider="openrouter"))
        == llm_http.DEFAULT_OPENROUTER_MODEL
    )
    assert (
        llm_http._openai_style_model(LlmConfig(provider="openrouter", model="x/y"))
        == "x/y"
    )


def test_openai_style_urls_follow_the_configured_base() -> None:
    assert llm_http._openai_style_url(LlmConfig(provider="openrouter")).startswith(
        "https://openrouter.ai/"
    )
    assert (
        llm_http._openai_style_url(
            LlmConfig(provider="custom", base_url="http://localhost:11434/v1/")
        )
        == "http://localhost:11434/v1/chat/completions"
    )


def test_provider_refusals_explain_themselves() -> None:
    import httpx

    response = httpx.Response(
        404,
        json={"error": {"message": "Model has been retired"}},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )

    message = llm_http.describe_http_error("OpenRouter", response)

    assert "404" in message
    assert "Model has been retired" in message
    assert "Pick a different model" in message


def _recorded_request(cfg: LlmConfig) -> Dict[str, Any]:
    """Run one completion and return the JSON body the provider received.

    Raises if nothing was sent: an unsent request looks like an absent
    field, and that is how "the setting is left out on purpose" once passed
    against a config the layer refused to use in the first place.
    """
    import asyncio

    captured: Dict[str, Any] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> Dict[str, Any]:
            return {
                "choices": [{"message": {"content": "<!DOCTYPE html></html>"}}],
                "usage": {},
            }

    async def _post(
        _self: object, _url: str, headers: Dict[str, Any] = {}, json: Dict[str, Any] = {}
    ) -> object:
        captured.update(json)
        return _Response()

    original = llm_http.httpx.AsyncClient.post
    llm_http.httpx.AsyncClient.post = _post  # type: ignore[assignment]
    try:
        asyncio.run(llm_http.complete(cfg, "system", "user", max_tokens=128, timeout=5))
    finally:
        llm_http.httpx.AsyncClient.post = original  # type: ignore[assignment]
    assert captured, "no request was sent, so nothing here was checked"
    return captured


def _config(**overrides: str) -> LlmConfig:
    return LlmConfig(
        provider="openrouter", model="a/b", api_key="test-key", **overrides
    )


def test_reasoning_effort_is_left_out_unless_it_is_asked_for() -> None:
    """An ordinary model should not receive a field it does not know."""
    body = _recorded_request(_config())

    assert "reasoning" not in body


def test_a_reasoning_model_can_be_asked_to_think_less() -> None:
    """Asking for a page made the model think for 259s and return nothing.

    With `low` the same prompt came back as a whole page in 34s, so the
    setting has to reach the provider rather than being a local guess.
    """
    body = _recorded_request(_config(reasoning_effort="low"))

    assert body["reasoning"] == {"effort": "low"}
    assert body["messages"], "the rest of the request must be unchanged"


def test_a_blank_effort_is_never_sent() -> None:
    """An empty string is what an untouched setting holds."""
    body = _recorded_request(_config(reasoning_effort=""))

    assert "reasoning" not in body


def test_the_effort_survives_the_round_trip_through_a_dict() -> None:
    """Runs are resumed from stored parameters, not from a live config."""
    restored = LlmConfig.from_dict(
        {"provider": "openrouter", "model": "a/b", "reasoning_effort": "low"}
    )

    assert restored.reasoning_effort == "low"


def test_the_configured_model_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENROUTER_MODEL used to be read by nobody.

    With the model left empty the request fell back to the built-in default,
    so a gateway configured through .env answered for a different model than
    the one it was set up for - here a 404 on a retired free slug.
    """
    import config as config_module
    from routes import url_to_code

    monkeypatch.setattr(url_to_code, "OPENROUTER_MODEL", "chosen/from-env", raising=False)
    monkeypatch.setattr(url_to_code, "OPENROUTER_API_KEY", "sk-env", raising=False)

    chosen = url_to_code._llm_config_for(url_to_code.UrlToCodeParams(url="https://x.test"))

    assert chosen.provider == "openrouter"
    assert chosen.model == "chosen/from-env"
    assert config_module is not None


def test_worker_params_are_read_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The crawler's parameters carry an API key, so they never hit argv."""
    import io as _io
    import sys

    monkeypatch.setattr(sys, "stdin", _io.StringIO('{"url": "https://example.com"}'))

    assert llm_http.read_worker_params(["worker.py"]) == {"url": "https://example.com"}
