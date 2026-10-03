"""Picking the model for the job.

A clone makes three kinds of model call, and they are not equally worth
money. Rebuilding a page from eight thousand characters of markup is the
whole point and deserves the best model available. The landing page's
project-structure prompt is a summary. The repairs are a judgement about a
diff the user can already see.

The temptation is to send everything to the one model the user picked, and
the trap is that the user picked a single model for a *feature* they care
about. The rule here is narrow on purpose: the main event never moves, and
only the auxiliary calls are allowed to drop to a cheaper model - and only
when that model is one the user has actually configured a key for, because
a call that cannot authenticate costs a failed attempt and tells the user
nothing about the cost.

This is a suggestion, not a policy. The expensive model is always available
and always the default; turning the cheaper path off is a single setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import clone_cost
import vision
from llm_http import LlmConfig

# The three kinds of call a clone makes.
PAGE = "page"            # rebuilding a page from its markup
STRUCTURE = "structure"  # the landing page and the database schema
REPAIR = "repair"        # correcting one page after a visual diff

# Only these may be handed to a cheaper model. A page rebuild is the
# product; sending it somewhere else to save money is not a trade the tool
# gets to make on the user's behalf.
DOWNGRADEABLE = frozenset({STRUCTURE, REPAIR})


@dataclass
class Routing:
    """Which model each kind of call goes to, and why."""

    page_model: str
    auxiliary_model: str
    reasons: Dict[str, str]
    # What the chosen model can see: vision.YES, vision.NO or vision.UNKNOWN.
    # The page build and the visual repair need a model that looks at
    # pictures, so this decides whether the cheaper model is offered at all.
    vision: str = "unknown"

    def model_for(self, kind: str) -> str:
        return self.auxiliary_model if kind in DOWNGRADEABLE else self.page_model

    def to_json(self) -> Dict[str, Any]:
        return {
            "pageModel": self.page_model,
            "auxiliaryModel": self.auxiliary_model,
            "reasons": self.reasons,
            "vision": self.vision,
            "seesImages": self.vision == vision.YES,
        }


def configured_providers(params: Dict[str, Any]) -> Dict[str, str]:
    """Provider -> model, for the providers the request actually has a key for."""
    pairs = (
        ("openai", params.get("openAiApiKey"), params.get("openAiModel") or "gpt-4o"),
        ("anthropic", params.get("anthropicApiKey"), params.get("anthropicModel")),
        ("gemini", params.get("geminiApiKey"), params.get("geminiModel")),
        ("openrouter", params.get("openRouterApiKey"), params.get("openRouterModel")),
    )
    found: Dict[str, str] = {}
    for provider, key, model in pairs:
        if key and model:
            found[provider] = str(model)
    return found


def route(
    params: Dict[str, Any],
    chosen: LlmConfig,
    use_cheaper: bool = True,
    vision_capability: str = vision.UNKNOWN,
) -> Routing:
    """Decide which model each kind of call goes to.

    `chosen` is the model the user asked for, and the rule is simple: the
    chosen model gets everything.

    The cheaper path exists only for a chosen model that cannot look at
    pictures, because then the summary and the repair calls are the only
    ones the tool makes - a model that cannot see cannot be shown the two
    screenshots a repair is built from, so those calls degrade to
    descriptions in words. A model that can see is asked to do all of it,
    which is what picking a model in this tool means.

    `vision_capability` is vision.YES, vision.NO or vision.UNKNOWN. It is
    passed in rather than probed here so that this stays a pure function
    and the one call that costs money is made once, by the caller.
    """
    page_model = chosen.model or ""
    reasons: Dict[str, str] = {}
    auxiliary = page_model

    if vision_capability == vision.YES:
        reasons["auxiliary"] = (
            f"{page_model} can see images, so it does every part of the job: "
            "pages, the structure summary and the visual repairs"
        )
        return Routing(
            page_model=page_model,
            auxiliary_model=auxiliary,
            reasons=reasons,
            vision=vision_capability,
        )

    if vision_capability == vision.NO:
        reasons["vision"] = (
            f"{page_model} cannot see images, so the visual repair is made "
            "from a description of the differences rather than from the "
            "screenshots"
        )
    else:
        reasons["vision"] = (
            f"Could not confirm whether {page_model} can see images, so the "
            "visual repair falls back to describing the differences"
        )

    if not use_cheaper:
        reasons["auxiliary"] = "using the chosen model for everything"
        return Routing(
            page_model=page_model,
            auxiliary_model=auxiliary,
            reasons=reasons,
            vision=vision_capability,
        )

    available = configured_providers(params)
    for provider, model in available.items():
        if model == page_model:
            continue
        saving = clone_cost.cost_of(page_model, 4_000, 8_000) - clone_cost.cost_of(
            model, 4_000, 8_000
        )
        if saving > 0:
            auxiliary = model
            reasons["auxiliary"] = (
                f"{model} is cheaper than {page_model} for the summary and "
                f"repair calls; pages still use {page_model}"
            )
            break
    else:
        # Nothing configured is cheaper, which is the common case: a user
        # with one key has exactly one model to pick from.
        reasons["auxiliary"] = "no cheaper model is configured"

    return Routing(
        page_model=page_model,
        auxiliary_model=auxiliary,
        reasons=reasons,
        vision=vision_capability,
    )


def config_for(cfg: LlmConfig, model: str) -> LlmConfig:
    """The same provider and key, pointed at another model.

    The key is carried over deliberately: the user configured it for this
    provider, and a different model on the same account is a different
    question to the same bill.
    """
    if not model or model == cfg.model:
        return cfg
    return LlmConfig(
        provider=cfg.provider,
        model=model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
    )


def estimate(cfg: LlmConfig, pages: int) -> Dict[str, Any]:
    """What a run of this many pages is likely to cost, and its parts."""
    total = clone_cost.estimate_page_cost(cfg.model, pages)
    per_page = clone_cost.estimate_page_cost(cfg.model, 1)
    return {
        "pages": pages,
        "model": cfg.model,
        "perPage": round(per_page, 4),
        "perPageText": clone_cost.describe_money(per_page),
        "total": round(total, 2),
        "totalText": clone_cost.describe_money(total),
    }


__all__ = [
    "DOWNGRADEABLE",
    "PAGE",
    "REPAIR",
    "STRUCTURE",
    "Routing",
    "config_for",
    "configured_providers",
    "estimate",
    "route",
]
