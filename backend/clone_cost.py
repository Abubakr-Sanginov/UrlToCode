"""What a clone costs, and when to stop.

Cloning a site is a lot of model calls in a row, and the user has no way to
see the meter while it happens. Two providers at different prices, a crawl
that turned up forty pages instead of five, a repair pass that re-asks for
every page below the bar - each of those is money, and the tool that spends
it is not the one holding the card.

So the cost is worked out per call, added up per run, and checked before the
next call rather than after the last one. The ceiling is a hard stop: a run
that would pass it stops with an explanation, because a silent overspend
that the user discovers on an invoice is the worst possible outcome of a
convenience feature.

The prices are the ones the providers publish, per million tokens, and they
are approximations kept in one place so they can be corrected without
touching the arithmetic. Where a price is unknown the call still happens and
still counts towards the token total: refusing to run because we cannot
price a model would be worse than running it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

# USD per million tokens, as (input, output). A model missing from this map
# is still counted, just not priced.
PRICES: Dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}

# A page of a cloned site is a long document. Four thousand tokens of markup
# in, eight thousand out is a typical page, and it is what the per-page
# estimate below assumes.
TYPICAL_PAGE_INPUT_TOKENS = 4_000
TYPICAL_PAGE_OUTPUT_TOKENS = 8_000

# What a run costs when nothing is known about the model: a pessimistic
# mid-range price, so an unpriced model never makes the estimate look free.
FALLBACK_PRICE = (3.00, 15.00)


def price_for(model: str) -> tuple[float, float]:
    """Input and output price per million tokens for a model.

    Matched on the id the provider was given, so a dated or suffixed id
    ("gpt-4o-2024-08-06", "claude-3-5-sonnet-latest") is priced like the
    model it names rather than falling through to "unknown".
    """
    name = (model or "").strip().lower()
    if not name:
        return FALLBACK_PRICE
    if name in PRICES:
        return PRICES[name]
    # Longest matching prefix wins, so "gpt-4o-mini" beats "gpt-4o" and a
    # model that does not exist cannot be priced as a more expensive one.
    best = ""
    for known in PRICES:
        if name.startswith(known) and len(known) > len(best):
            best = known
    return PRICES[best] if best else FALLBACK_PRICE


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    """What one call cost, in dollars."""
    per_in, per_out = price_for(model)
    return (max(0, input_tokens) * per_in + max(0, output_tokens) * per_out) / 1_000_000


def estimate_page_cost(model: str, pages: int) -> float:
    """What a run of `pages` page generations is likely to cost.

    An estimate and not a promise: a page with a lot of markup in it costs
    more, and one that comes back short costs less. It exists so the user
    can see the order of magnitude before starting, which is the only point
    at which that number is still actionable.
    """
    return cost_of(model, TYPICAL_PAGE_INPUT_TOKENS * pages, TYPICAL_PAGE_OUTPUT_TOKENS * pages)


class CeilingExceeded(Exception):
    """The run would cost more than the user allowed."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(
            f"This run reached the {describe_money(ceiling)} limit after "
            f"{describe_money(spent)}. Everything it has finished is saved - "
            f"raise the limit in Settings, or regenerate the missing pages "
            f"one at a time."
        )
        self.spent = spent
        self.ceiling = ceiling


def describe_money(amount: float) -> str:
    """Money in the units it is actually worth reading in."""
    if amount >= 1.0:
        return f"${amount:.2f}"
    if amount >= 0.01:
        return f"${amount:.3f}".rstrip("0").rstrip(".")
    return f"${amount * 100:.1f}¢"


@dataclass
class Spend:
    """What one run has cost so far."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    models: List[str] = field(default_factory=lambda: cast(List[str], []))

    def add(self, model: str, input_tokens: int, output_tokens: int) -> float:
        self.calls += 1
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.cost += cost_of(model, input_tokens, output_tokens)
        if model and model not in self.models:
            self.models.append(model)
        return self.cost

    def to_json(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cost": round(self.cost, 4),
            "costText": describe_money(self.cost),
            "models": self.models,
        }


class Budget:
    """A ceiling for one run, checked before each model call.

    A ceiling of zero means no ceiling: "0" is what an unset field arrives
    as, and treating it as "spend nothing" would silently break every run
    started without setting a limit.
    """

    def __init__(self, ceiling: float = 0.0) -> None:
        self.ceiling = max(0.0, ceiling)
        self.spend = Spend()

    @property
    def unlimited(self) -> bool:
        return self.ceiling <= 0.0

    @property
    def remaining(self) -> float:
        return float("inf") if self.unlimited else max(0.0, self.ceiling - self.spend.cost)

    def can_afford(self, model: str, pages: int = 1) -> bool:
        """Whether starting this call would stay inside the ceiling."""
        if self.unlimited:
            return True
        return self.spend.cost + estimate_page_cost(model, pages) <= self.ceiling

    def check(self, model: str, pages: int = 1) -> None:
        if not self.can_afford(model, pages):
            raise CeilingExceeded(self.spend.cost, self.ceiling)

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.spend.add(model, input_tokens, output_tokens)

    def to_json(self) -> Dict[str, Any]:
        body = self.spend.to_json()
        body["ceiling"] = round(self.ceiling, 2)
        body["ceilingText"] = describe_money(self.ceiling) if not self.unlimited else "none"
        body["remaining"] = None if self.unlimited else round(self.remaining, 4)
        return body


def tokens_of(text: str) -> int:
    """A token count good enough to price a call with.

    Providers report their own counts, and this is only the fallback for a
    gateway that does not. Four characters per token is the usual English
    average and is within a factor of two either way - close enough to show
    a number, not so close that it should be presented as the real figure.
    """
    return max(0, len(text) // 4)


def record_from_usage(
    budget: Budget, model: str, usage: Optional[Dict[str, Any]], prompt: str, answer: str
) -> None:
    """Add one call to the budget, preferring the provider's own counts."""
    if isinstance(usage, dict):
        raw_in = usage.get("prompt_tokens", usage.get("input_tokens"))
        raw_out = usage.get("completion_tokens", usage.get("output_tokens"))
        try:
            in_tokens = int(raw_in) if raw_in is not None else tokens_of(prompt)
            out_tokens = int(raw_out) if raw_out is not None else tokens_of(answer)
        except (TypeError, ValueError):
            in_tokens, out_tokens = tokens_of(prompt), tokens_of(answer)
    else:
        in_tokens, out_tokens = tokens_of(prompt), tokens_of(answer)
    budget.record(model, in_tokens, out_tokens)
