"""Does a user's own endpoint get asked, and are the pictures real?

The custom provider is the one path where the model has a name nobody has
ever heard of, which is exactly the case a name list cannot answer. These
tests stand up a real HTTP server that speaks the OpenAI chat shape and
check that the probe reaches it, that the image actually arrives as image
content, and that what the server says is what we report.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

import pytest

import clone_routing
import vision
from llm_http import Image, LlmConfig, complete
from routes.url_to_code import _llm_config_for, UrlToCodeParams


class FakeProvider:
    """An OpenAI-compatible server that answers whatever it is told to."""

    def __init__(self, reply: str = "green", status: int = 200) -> None:
        self.reply = reply
        self.status = status
        self.requests: List[Dict[str, Any]] = []
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "FakeProvider":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakeProvider":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    def _handler(self) -> Any:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass  # keep the test output quiet

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                provider.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                if provider.status != 200:
                    body = json.dumps(
                        {"error": {"message": "model not available"}}
                    ).encode()
                    self.send_response(provider.status)
                else:
                    body = json.dumps(
                        {
                            "choices": [
                                {"message": {"content": provider.reply}, "finish_reason": "stop"}
                            ],
                            "usage": {"prompt_tokens": 40, "completion_tokens": 2},
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def params_for(url: str, model: str = "llava-v1.6") -> UrlToCodeParams:
    return UrlToCodeParams(
        url="https://x.test",
        custom_provider_base_url=url,
        custom_provider_model=model,
    )


@pytest.fixture()
def provider() -> Any:
    with FakeProvider() as running:
        yield running


# --- the endpoint is chosen --------------------------------------------------


def test_a_custom_provider_becomes_the_chosen_config(provider: FakeProvider) -> None:
    # A user's own endpoint outranks the others, so it is the one that has
    # to be asked.
    cfg = _llm_config_for(params_for(provider.url))

    assert cfg.provider == "custom"
    assert cfg.base_url == provider.url
    assert cfg.model == "llava-v1.6"
    assert cfg.is_usable


def test_a_custom_provider_needs_no_key(provider: FakeProvider) -> None:
    # Local servers usually run without one, and refusing to ask a model
    # that would have answered is how a working setup looks broken.
    cfg = _llm_config_for(params_for(provider.url))

    assert cfg.api_key == "no-key"
    assert cfg.is_usable


def test_a_self_hosted_model_of_an_unknown_name_is_not_guessed_at() -> None:
    # The situation a name list is worst at, and the reason the probe
    # exists: nobody knows what "my-org/finetune-7b" can see.
    assert vision.known_capability("my-org/finetune-7b") == vision.UNKNOWN


# --- the probe really goes there ---------------------------------------------


async def test_the_probe_reaches_the_users_own_endpoint(
    provider: FakeProvider,
) -> None:
    answer = await vision.detect(_llm_config_for(params_for(provider.url)), force=True)

    assert answer.capability == vision.YES
    assert answer.source == "probe"
    assert len(provider.requests) == 1
    assert provider.requests[0]["model"] == "llava-v1.6"


async def test_the_picture_arrives_as_an_image_block(provider: FakeProvider) -> None:
    await vision.detect(_llm_config_for(params_for(provider.url)), force=True)

    content = provider.requests[0]["messages"][-1]["content"]
    kinds = [part["type"] for part in content]

    # A model that cannot read images would pass this test too if the
    # picture were sent as text, so the shape is the thing being checked.
    assert kinds == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_what_the_model_says_is_what_is_reported() -> None:
    with FakeProvider(reply="yellow") as provider:
        answer = await vision.detect(
            _llm_config_for(params_for(provider.url)), force=True
        )

    # A self-hosted model is the case a name list is worst at, which is why
    # the answer comes from the server rather than from the name.
    assert answer.capability == vision.NO


async def test_an_endpoint_that_refuses_is_not_called_blind() -> None:
    with FakeProvider(status=503) as provider:
        answer = await vision.detect(
            _llm_config_for(params_for(provider.url)), force=True
        )

    assert answer.capability == vision.UNKNOWN
    assert "would not serve" in answer.detail


async def test_the_answer_is_kept_per_endpoint() -> None:
    # Two servers can both serve the same id and only one of them can see,
    # so the answer is remembered per endpoint rather than per name.
    with FakeProvider(reply="green") as first, FakeProvider(reply="red") as second:
        one = await vision.detect(_llm_config_for(params_for(first.url)), force=True)
        two = await vision.detect(_llm_config_for(params_for(second.url)), force=True)

    assert (one.capability, two.capability) == (vision.YES, vision.NO)


# --- what it means for routing -----------------------------------------------


async def test_a_self_hosted_vision_model_does_all_the_work(provider: FakeProvider) -> None:
    cfg = _llm_config_for(params_for(provider.url))

    routing = clone_routing.route(
        params_for(provider.url).__dict__, cfg, vision_capability=vision.YES
    )

    assert routing.model_for(clone_routing.REPAIR) == "llava-v1.6"
    assert routing.to_json()["seesImages"] is True


async def test_the_whole_chain_from_settings_to_the_answer(provider: FakeProvider) -> None:
    # Exactly what the settings dialog puts on the wire, through the field
    # names the frontend uses.
    chosen = _llm_config_for(
        UrlToCodeParams(
            url="https://x.test",
            custom_provider_base_url=provider.url,
            custom_provider_model="llava-v1.6",
            # An OpenAI key alongside it must not win: the user's own
            # endpoint is the one they chose.
            openai_api_key="sk-should-be-ignored",
        )
    )
    answer = await vision.detect(chosen, force=True)

    assert chosen.provider == "custom"
    assert answer.sees_images is True
    assert provider.requests


# --- a provider that ignores the picture -------------------------------------


async def test_an_endpoint_that_will_not_look_is_reported_as_blind() -> None:
    # A text-only server on the user's own machine answers the question in
    # words instead of reading the picture. Believed rather than filed under
    # "unknown", so the user is told what the model actually said.
    with FakeProvider(reply="I cannot view images.") as provider:
        answer = await vision.detect(
            _llm_config_for(params_for(provider.url)), force=True
        )

    assert answer.capability == vision.NO


async def test_a_custom_provider_without_a_base_url_is_not_called() -> None:
    cfg = LlmConfig(provider="custom", model="llava-v1.6", api_key="no-key")

    # Nothing to call, so nothing is asked and nothing is invented.
    result = await complete(cfg, "prompt", "turn", images=[Image(data=b"x")])

    assert not result.text
    assert "base URL" in (result.empty_reason or "")
