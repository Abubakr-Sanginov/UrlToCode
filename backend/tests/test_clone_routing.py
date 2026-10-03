"""Choosing a model for the job, and not for the user's bill by surprise.

The rule under test is narrow: pages are built with the model the user
chose, and only the auxiliary calls may move. Everything else here is the
tool declining to be clever.
"""

from typing import Dict

import pytest

import clone_routing
import vision
from llm_http import LlmConfig


def cfg(provider: str = "anthropic", model: str = "claude-opus-4") -> LlmConfig:
    return LlmConfig(provider=provider, model=model, api_key="k")


def params(**overrides: object) -> Dict[str, object]:
    values: Dict[str, object] = {
        "openAiApiKey": None,
        "openAiModel": None,
        "anthropicApiKey": None,
        "anthropicModel": None,
        "geminiApiKey": None,
        "geminiModel": None,
        "openRouterApiKey": None,
        "openRouterModel": None,
    }
    values.update(overrides)
    return values


def test_a_model_that_can_see_does_every_part_of_the_job() -> None:
    # This is the rule the user asked for: pick a model that sees, and it
    # does the pages, the summary and the repairs alike.
    routing = clone_routing.route(
        params(geminiApiKey="g", geminiModel="gemini-2.5-flash"),
        cfg(),
        vision_capability=vision.YES,
    )

    assert routing.model_for(clone_routing.PAGE) == "claude-opus-4"
    assert routing.model_for(clone_routing.STRUCTURE) == "claude-opus-4"
    assert routing.model_for(clone_routing.REPAIR) == "claude-opus-4"
    assert routing.vision == vision.YES
    assert routing.to_json()["seesImages"] is True


def test_a_model_that_can_see_is_not_replaced_by_a_cheaper_one() -> None:
    # Moving a visual model to a cheaper text-only one would quietly turn the
    # visual repair back into a guess, which is the thing being fixed.
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="gpt-4o-mini"),
        cfg(),
        vision_capability=vision.YES,
    )

    assert routing.auxiliary_model == "claude-opus-4"


def test_a_model_that_cannot_see_is_told_so_rather_than_pretending() -> None:
    routing = clone_routing.route(params(), cfg(), vision_capability=vision.NO)

    assert routing.reasons["vision"]
    assert "cannot see images" in routing.reasons["vision"]
    assert routing.to_json()["seesImages"] is False


def test_an_unconfirmed_model_is_not_treated_as_blind_certainly() -> None:
    # "We could not check" and "it cannot" send the user to different places.
    routing = clone_routing.route(params(), cfg(), vision_capability=vision.UNKNOWN)

    assert "Could not confirm" in routing.reasons["vision"]


def test_a_blind_model_still_may_send_the_summary_to_a_cheaper_one() -> None:
    # With no vision there is no visual repair to keep on the chosen model,
    # so the cheaper path is still offered for the summary.
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="gpt-4o"),
        cfg(),
        vision_capability=vision.NO,
    )

    assert routing.auxiliary_model == "gpt-4o"
    assert routing.model_for(clone_routing.PAGE) == "claude-opus-4"


def test_turning_the_cheaper_path_off_still_applies_to_a_blind_model() -> None:
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="gpt-4o"),
        cfg(),
        use_cheaper=False,
        vision_capability=vision.NO,
    )

    assert routing.auxiliary_model == "claude-opus-4"


def test_no_capability_given_keeps_the_old_behaviour() -> None:
    # Anything that has not said otherwise behaves as it did before vision
    # existed, rather than being blocked behind a new required argument.
    routing = clone_routing.route(params(openAiApiKey="sk", openAiModel="gpt-4o"), cfg())

    assert routing.auxiliary_model == "gpt-4o"
    assert routing.vision == vision.UNKNOWN


# --- the rule ----------------------------------------------------------------


def test_pages_are_always_built_with_the_model_the_user_chose() -> None:
    # This is the product. Nothing about saving money moves it.
    routing = clone_routing.route(
        params(geminiApiKey="g", geminiModel="gemini-2.5-flash"), cfg()
    )

    assert routing.model_for(clone_routing.PAGE) == "claude-opus-4"


def test_a_cheaper_model_takes_only_the_auxiliary_calls() -> None:
    routing = clone_routing.route(
        params(geminiApiKey="g", geminiModel="gemini-2.5-flash"), cfg()
    )

    assert routing.model_for(clone_routing.STRUCTURE) == "gemini-2.5-flash"
    assert routing.model_for(clone_routing.REPAIR) == "gemini-2.5-flash"


def test_a_user_with_one_key_keeps_that_model_for_everything() -> None:
    # The common case: one provider, one model. There is nothing to route to,
    # and saying so is better than inventing a preference the user did not
    # express.
    routing = clone_routing.route(
        params(anthropicApiKey="k", anthropicModel="claude-opus-4"), cfg()
    )

    assert routing.auxiliary_model == "claude-opus-4"
    assert "no cheaper model is configured" in routing.reasons["auxiliary"]


def test_a_model_with_no_key_is_not_used() -> None:
    # A model the user cannot authenticate against fails every time, and the
    # failures cost time and tell them nothing useful about cost.
    routing = clone_routing.route(
        params(geminiModel="gemini-2.5-flash", geminiApiKey=None), cfg()
    )

    assert routing.auxiliary_model == "claude-opus-4"


def test_an_expensive_model_is_not_replaced_by_a_dearer_one() -> None:
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="claude-opus-4"), cfg("gemini", "gemini-2.5-flash")
    )

    assert routing.auxiliary_model == "gemini-2.5-flash"


def test_the_cheaper_model_can_be_turned_off() -> None:
    routing = clone_routing.route(
        params(geminiApiKey="g", geminiModel="gemini-2.5-flash"), cfg(), use_cheaper=False
    )

    assert routing.auxiliary_model == routing.page_model
    assert routing.model_for(clone_routing.REPAIR) == "claude-opus-4"


def test_a_second_configured_model_is_used_when_it_is_cheaper() -> None:
    # A user with two keys has expressed a preference for each; picking the
    # cheaper one for the calls that do not need the best model is the point.
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="gpt-4o"), cfg()
    )

    assert routing.auxiliary_model == "gpt-4o"
    assert routing.model_for(clone_routing.PAGE) == "claude-opus-4"


def test_the_reason_is_shown_rather_than_a_silent_switch() -> None:
    routing = clone_routing.route(
        params(geminiApiKey="g", geminiModel="gemini-2.5-flash"), cfg()
    )

    assert "gemini-2.5-flash" in routing.reasons["auxiliary"]
    assert "claude-opus-4" in routing.reasons["auxiliary"]


def test_a_cheaper_model_that_cannot_be_priced_is_still_used_when_configured() -> None:
    # The user's own gateway may cost nothing at all. Refusing to route to
    # it because our price list has never heard of it would be backwards.
    routing = clone_routing.route(
        params(openAiApiKey="sk", openAiModel="my-local-model"), cfg()
    )

    assert routing.auxiliary_model == "my-local-model"


# --- keeping the credentials -------------------------------------------------


def test_a_different_model_on_the_same_provider_keeps_the_key() -> None:
    switched = clone_routing.config_for(cfg("openai", "claude-opus-4"), "gpt-4o-mini")

    assert switched.model == "gpt-4o-mini"
    assert switched.provider == "openai"
    assert switched.api_key == "k"


def test_asking_for_the_model_already_in_use_changes_nothing() -> None:
    original = cfg()

    assert clone_routing.config_for(original, "claude-opus-4") is original


def test_an_empty_model_does_not_blank_the_configuration() -> None:
    original = cfg()

    assert clone_routing.config_for(original, "") is original


# --- what the user is told before starting -----------------------------------


def test_the_estimate_scales_with_the_number_of_pages() -> None:
    one = clone_routing.estimate(cfg("openai", "gpt-4o"), 1)
    ten = clone_routing.estimate(cfg("openai", "gpt-4o"), 10)

    assert ten["total"] == pytest.approx(one["total"] * 10, rel=0.01)
    assert one["total"] > 0


def test_the_estimate_is_shown_in_money_a_person_can_read() -> None:
    estimate = clone_routing.estimate(cfg("openai", "gpt-4o"), 10)

    assert estimate["totalText"].startswith("$")
    assert estimate["perPageText"].startswith("$")
    assert estimate["model"] == "gpt-4o"


def test_the_estimate_names_the_model_it_was_made_for() -> None:
    # An estimate for a model the user is not using is worse than none.
    assert clone_routing.estimate(cfg("openai", "gpt-4o"), 5)["model"] == "gpt-4o"
