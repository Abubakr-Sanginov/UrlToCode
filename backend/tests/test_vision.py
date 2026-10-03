"""Does the model look at pictures?

The rule these tests defend is that the answer is established by asking,
and that the three answers stay distinct. A tool that says "cannot see"
about a model it was never allowed to call has told the user to buy a
different model for a problem that model does not have.
"""

import io
from typing import Any, List, Optional

import pytest

import clone_routing
import llm_http
import vision
from llm_http import Completion, Image, LlmConfig, ProviderError


def cfg(model: str = "made-up-model") -> LlmConfig:
    return LlmConfig(provider="openai", model=model, api_key="k")


@pytest.fixture(autouse=True)
def no_shared_answers() -> Any:
    vision.clear_cache()
    yield
    vision.clear_cache()


def answers_with(text: str) -> Any:
    async def fake_complete(*args: Any, **kwargs: Any) -> Completion:
        return Completion(text=text)

    return fake_complete


def refuses(message: str = "OpenAI returned 402: subscription required") -> Any:
    async def fake_complete(*args: Any, **kwargs: Any) -> Completion:
        raise ProviderError(message)

    return fake_complete


# --- the name, when the name settles it -------------------------------------


def test_a_gateway_prefix_is_stripped_before_matching() -> None:
    # "free/gpt-4o" is a routing label on that gateway, not part of the
    # model's name, and matching against it would classify every free model
    # as text-only.
    assert vision.known_capability("free/gpt-4o") == vision.YES
    assert vision.known_capability("anthropic/claude-opus-4.6") == vision.YES


def test_a_known_vision_model_is_still_asked() -> None:
    # The name list is not the authority. A gateway can serve the same id
    # from something that does not see, and "supports vision" is a property
    # of the deployment as much as of the name.
    assert vision.known_capability("claude-opus-4-6") == vision.YES
    assert vision.known_capability("gemini-3.1-pro") == vision.YES


async def test_the_name_is_only_used_when_there_is_no_key_to_ask_with() -> None:
    # With no key there is nothing to probe, and a guess labelled as a guess
    # is better than nothing at all.
    answer = await vision.detect(LlmConfig(provider="openai", model="gpt-4o"))

    assert answer.capability == vision.YES
    assert answer.source == "name"
    assert "no key" in answer.detail


async def test_a_model_with_a_key_is_asked_even_when_its_name_is_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: List[int] = []

    async def counting(*args: Any, **kwargs: Any) -> Completion:
        asked.append(1)
        return Completion(text="green")

    monkeypatch.setattr(vision, "complete", counting)

    answer = await vision.detect(cfg("gpt-4o"), force=True)

    assert len(asked) == 1
    assert answer.source == "probe"
    assert answer.capability == vision.YES


def test_a_known_text_model_says_so() -> None:
    assert vision.known_capability("deepseek-v4-pro") == vision.NO


def test_a_model_nobody_has_heard_of_is_not_guessed_at() -> None:
    # Guessing here is how a model that sees gets told it does not.
    assert vision.known_capability("some-new-thing-2027") == vision.UNKNOWN
    assert vision.known_capability("") == vision.UNKNOWN


# --- the probe ---------------------------------------------------------------


async def test_a_model_that_answers_correctly_can_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision, "complete", answers_with("Green."))

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.YES
    assert answer.sees_images is True


async def test_a_model_that_names_the_wrong_colour_cannot_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # It answered, which is the point: the answer is wrong, not missing.
    monkeypatch.setattr(vision, "complete", answers_with("red"))

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.NO


async def test_a_provider_that_will_not_serve_the_model_is_not_called_blind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The distinction that matters to the user: they have no access, which
    # is not the same as the model being unable to see.
    monkeypatch.setattr(vision, "complete", refuses())

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.UNKNOWN
    assert "subscription" in answer.detail


async def test_an_answer_with_no_colour_in_it_is_not_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model that says outright it cannot see is reporting its own
    # blindness, and that is worth believing: a model that can see the
    # picture has no reason to claim otherwise. Left as "unknown" it sat in
    # the middle forever, with the user told nothing about a model that had
    # already given the verdict.
    monkeypatch.setattr(vision, "complete", answers_with("I cannot view images."))

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.NO


async def test_an_answer_that_says_nothing_usable_is_still_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Talking at length without naming a colour and without saying it is
    # blind is genuinely ambiguous, and guessing either way would be a lie.
    monkeypatch.setattr(vision, "complete", answers_with("Let me think about that."))

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.UNKNOWN


async def test_the_probe_asks_for_a_guess_so_a_refusal_cannot_hide_blindness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refusal is ambiguous on its own - it can mean "I am blind" or "I am
    # being careful". Asking for a best guess makes the answer checkable.
    monkeypatch.setattr(vision, "complete", answers_with("green"))
    await vision.detect(cfg(), force=True)

    assert "guess" in vision._PROBE_QUESTION.lower()


async def test_a_model_that_says_nothing_is_not_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def silent(*args: Any, **kwargs: Any) -> Completion:
        return Completion(empty_reason="the model returned nothing")

    monkeypatch.setattr(vision, "complete", silent)

    answer = await vision.detect(cfg(), force=True)

    assert answer.capability == vision.UNKNOWN


async def test_the_probe_actually_shows_the_model_a_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Otherwise "it can see" is being decided by a text-only call, which is
    # the bug this whole module exists to fix.
    seen: List[List[object]] = []

    async def capture(*args: Any, **kwargs: Any) -> Completion:
        seen.append(kwargs.get("images") or [])
        return Completion(text="green")

    monkeypatch.setattr(vision, "complete", capture)
    await vision.detect(cfg(), force=True)

    assert len(seen) == 1
    assert len(seen[0]) == 1
    assert isinstance(seen[0][0], Image)


async def test_an_answer_is_remembered_rather_than_asked_for_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: List[int] = []

    async def counting(*args: Any, **kwargs: Any) -> Completion:
        calls.append(1)
        return Completion(text="green")

    monkeypatch.setattr(vision, "complete", counting)
    await vision.detect(cfg(), force=True)
    await vision.detect(cfg())

    assert len(calls) == 1


async def test_asking_again_on_purpose_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A gateway can change what an id points at, so "refresh" has to mean it.
    calls: List[int] = []

    async def counting(*args: Any, **kwargs: Any) -> Completion:
        calls.append(1)
        return Completion(text="green")

    monkeypatch.setattr(vision, "complete", counting)
    await vision.detect(cfg(), force=True)
    await vision.detect(cfg(), force=True)

    assert len(calls) == 2


async def test_no_model_chosen_is_unknown_not_no(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = await vision.detect(LlmConfig(provider="openai", model=""))

    assert answer.capability == vision.UNKNOWN


# --- the probe picture -------------------------------------------------------


def test_the_probe_picture_is_small_enough_to_be_free() -> None:
    # A few hundred bytes, because this runs on every model the user looks
    # at and the answer only depends on four colours.
    assert len(vision._probe_png()) < 5_000


def test_the_probe_picture_is_a_real_png() -> None:
    from PIL import Image as PILImage

    picture = PILImage.open(io.BytesIO(vision._probe_png()))

    assert picture.format == "PNG"


def test_the_colour_asked_about_is_the_one_in_the_picture() -> None:
    # The question and the picture have to agree, or a working model scores
    # as blind and the visual repair is disabled for no reason.
    from PIL import Image as PILImage

    picture = PILImage.open(io.BytesIO(vision._probe_png()))
    right = picture.crop(
        (
            picture.width - vision._PROBE_SIDE,
            0,
            picture.width,
            vision._PROBE_SIDE,
        )
    ).convert("RGB")

    assert right.getpixel((0, 0)) == (0, 128, 0)
    assert vision._PROBE_ANSWER == "green"


def test_an_image_with_no_bytes_is_not_an_image() -> None:
    with pytest.raises(ValueError):
        Image(data=b"")


def test_an_image_carries_its_own_encoding() -> None:
    encoded = Image(data=b"\x89PNG", mime_type="image/png").data_url()

    assert encoded.startswith("data:image/png;base64,")


# --- a failure must always be sayable ----------------------------------------


async def test_a_timeout_says_so_rather_than_reaching_the_user_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # str() of an httpx timeout is "". Passed through untouched it became a
    # blank error in the app, which is the one reply nobody can act on.
    import httpx

    async def times_out(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ReadTimeout("", request=httpx.Request("POST", "https://x.test"))

    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(times_out))

    with pytest.raises(ProviderError) as caught:
        await llm_http.complete(cfg(), "system", "user")

    assert "did not answer in time" in str(caught.value)


async def test_a_refused_connection_names_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    async def refuses(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError(
            "", request=httpx.Request("POST", "https://gateway.test/v1/chat")
        )

    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(refuses))

    with pytest.raises(ProviderError) as caught:
        await llm_http.complete(cfg(), "system", "user")

    assert "gateway.test" in str(caught.value)


async def test_no_network_failure_ever_produces_an_empty_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    class Odd(httpx.HTTPError):
        pass

    async def odd(*args: Any, **kwargs: Any) -> Any:
        raise Odd("")

    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(odd))

    with pytest.raises(ProviderError) as caught:
        await llm_http.complete(cfg(), "system", "user")

    assert str(caught.value).strip()


async def test_a_timeout_is_tried_again_before_it_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page costs minutes. A connection that wobbled used to throw all of
    # that away, which turned a pause into a lost run.
    import httpx

    attempts: List[int] = []

    async def wavers(*_args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("", request=httpx.Request("POST", "https://x.test"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "green"}}]},
            request=httpx.Request("POST", "https://x.test"),
        )

    monkeypatch.setattr(llm_http.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(wavers))

    result = await llm_http.complete(cfg(), "system", "user")

    assert result.text == "green"
    assert len(attempts) == 2


async def test_a_refusal_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bad key or a retired model fails the same way every time. Retrying
    # it only spends the user's time to reach the same refusal.
    import httpx

    attempts: List[int] = []

    async def refuses(*_args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise httpx.HTTPStatusError(
            "unauthorized",
            request=httpx.Request("POST", "https://x.test"),
            response=httpx.Response(401, request=httpx.Request("POST", "https://x.test")),
        )

    monkeypatch.setattr(llm_http.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(refuses))

    with pytest.raises(ProviderError):
        await llm_http.complete(cfg(), "system", "user")

    assert len(attempts) == 1


async def test_a_provider_that_stays_down_is_not_retried_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The retry is counted, not a loop: an endless retry would hang the run
    # instead of reporting it.
    import httpx

    attempts: List[int] = []

    async def always_down(*_args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise httpx.HTTPStatusError(
            "bad gateway",
            request=httpx.Request("POST", "https://x.test"),
            response=httpx.Response(502, request=httpx.Request("POST", "https://x.test")),
        )

    monkeypatch.setattr(llm_http.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_http.httpx, "AsyncClient", _fake_client(always_down))

    with pytest.raises(ProviderError):
        await llm_http.complete(cfg(), "system", "user")

    assert len(attempts) == llm_http.TRANSIENT_ATTEMPTS + 1


def test_a_timeout_leaves_room_for_a_slow_reasoning_model() -> None:
    # Measured on the model this project was tested against: 201s, 269s and
    # 303s for one prompt. At 300 the limit sat on top of the model's own
    # pace, so runs failed on whichever call happened to be slow.
    assert llm_http.REQUEST_TIMEOUT_SECONDS >= 600


async def _no_sleep(_seconds: float) -> None:
    return None


def _fake_client(handler: Any) -> Any:
    """An AsyncClient whose `.post()` raises whatever `handler` raises."""

    class Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            return await handler()

    return Client
