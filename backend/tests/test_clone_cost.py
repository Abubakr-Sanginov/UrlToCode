"""What a clone costs, and where it stops.

The arithmetic is the easy part. What these tests are really about is the
rule that money is checked *before* it is spent, and that a model nobody has
a price for is still counted rather than treated as free.
"""

from typing import Any, List, Optional

import pytest

import clone_cost
import clone_pages
from clone_runs import CloneRun
from crawler.crawler import CrawlPage, CrawlResult
from llm_http import Completion, LlmConfig


# --- pricing -----------------------------------------------------------------


def test_a_known_model_is_priced() -> None:
    assert clone_cost.price_for("gpt-4o") == (2.50, 10.00)


def test_a_dated_model_id_is_priced_as_the_model_it_names() -> None:
    # Providers hand out dated ids. Falling through to "unknown" here would
    # quote a wildly wrong figure for the same model.
    assert clone_cost.price_for("gpt-4o-2024-08-06") == clone_cost.price_for("gpt-4o")
    assert clone_cost.price_for("claude-3-5-sonnet-latest") == clone_cost.price_for(
        "claude-3-5-sonnet"
    )


def test_a_cheaper_variant_is_not_priced_as_the_expensive_one() -> None:
    # Longest prefix wins, or "gpt-4o-mini" would be billed as "gpt-4o" -
    # a twentyfold overstatement of what a run costs.
    assert clone_cost.price_for("gpt-4o-mini") == (0.15, 0.60)
    assert clone_cost.price_for("gpt-4o-mini") != clone_cost.price_for("gpt-4o")


def test_an_unknown_model_is_priced_pessimistically_not_free() -> None:
    # A model we have no price for still costs the user money. Showing zero
    # would be the one number that is certainly wrong.
    assert clone_cost.price_for("some-model-from-2027") == clone_cost.FALLBACK_PRICE
    assert clone_cost.price_for("") == clone_cost.FALLBACK_PRICE


def test_output_is_priced_higher_than_input() -> None:
    # Every current model charges more to answer than to read, and a page
    # clone is mostly output.
    per_in, per_out = clone_cost.price_for("gpt-4o")
    assert per_out > per_in
    assert clone_cost.cost_of("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)
    assert clone_cost.cost_of("gpt-4o", 0, 1_000_000) == pytest.approx(10.00)


def test_a_negative_token_count_cannot_make_a_call_look_free() -> None:
    assert clone_cost.cost_of("gpt-4o", -5_000, 0) == 0.0


def test_a_run_of_pages_is_estimated_before_it_starts() -> None:
    # The estimate exists so the user can see the order of magnitude at the
    # only moment it is still actionable: before starting.
    one = clone_cost.estimate_page_cost("gpt-4o", 1)
    ten = clone_cost.estimate_page_cost("gpt-4o", 10)
    assert ten == pytest.approx(one * 10)
    assert one > 0


# --- counting ----------------------------------------------------------------


def test_a_call_is_counted_and_priced() -> None:
    spend = clone_cost.Spend()

    spend.add("gpt-4o", 4_000, 8_000)

    assert spend.calls == 1
    assert spend.input_tokens == 4_000
    assert spend.output_tokens == 8_000
    assert spend.cost == pytest.approx(clone_cost.cost_of("gpt-4o", 4_000, 8_000))


def test_calls_add_up_across_models() -> None:
    spend = clone_cost.Spend()

    spend.add("gpt-4o", 1_000, 1_000)
    spend.add("gpt-4o-mini", 1_000, 1_000)

    assert spend.calls == 2
    assert spend.models == ["gpt-4o", "gpt-4o-mini"]
    assert spend.cost > clone_cost.cost_of("gpt-4o", 1_000, 1_000)


def test_the_provider_own_token_count_is_preferred() -> None:
    budget = clone_cost.Budget()

    clone_cost.record_from_usage(
        budget, "gpt-4o", {"prompt_tokens": 10, "completion_tokens": 20}, "x" * 4_000, "y" * 40_000
    )

    # The estimate would have said 1,000 and 10,000. The provider knows.
    assert budget.spend.input_tokens == 10
    assert budget.spend.output_tokens == 20


def test_a_gateway_that_reports_no_usage_is_still_counted() -> None:
    budget = clone_cost.Budget()

    clone_cost.record_from_usage(budget, "gpt-4o", None, "x" * 4_000, "y" * 4_000)

    # No usage is not zero usage.
    assert budget.spend.calls == 1
    assert budget.spend.cost > 0


def test_unreadable_usage_does_not_lose_the_money() -> None:
    budget = clone_cost.Budget()

    clone_cost.record_from_usage(
        budget, "gpt-4o", {"prompt_tokens": "lots"}, "x" * 4_000, "y" * 4_000
    )

    assert budget.spend.cost > 0


def test_geminis_own_names_are_understood() -> None:
    budget = clone_cost.Budget()

    clone_cost.record_from_usage(
        budget,
        "gemini-2.5-flash",
        {"prompt_tokens": 100, "completion_tokens": 200},
        "",
        "",
    )

    assert budget.spend.output_tokens == 200


# --- the ceiling -------------------------------------------------------------


def test_no_ceiling_means_no_ceiling() -> None:
    # Zero arrives from an unset field. Reading it as "spend nothing" would
    # silently break every run started without setting a limit.
    budget = clone_cost.Budget(0)
    assert budget.unlimited
    budget.record("gpt-4o", 10_000_000, 10_000_000)
    assert budget.can_afford("gpt-4o", 1)


def test_a_ceiling_stops_the_run_before_the_next_call() -> None:
    budget = clone_cost.Budget(0.05)
    budget.record("gpt-4o", 1_000_000, 1_000_000)  # $12.50, well past the cap

    assert not budget.can_afford("gpt-4o", 1)
    with pytest.raises(clone_cost.CeilingExceeded):
        budget.check("gpt-4o", 1)


def test_a_ceiling_says_how_much_was_already_spent() -> None:
    budget = clone_cost.Budget(0.05)
    budget.record("gpt-4o", 1_000_000, 1_000_000)

    with pytest.raises(clone_cost.CeilingExceeded) as raised:
        budget.check("gpt-4o", 1)

    assert raised.value.spent == pytest.approx(12.50)
    assert "$12.50" in str(raised.value)
    assert "$0.05" in str(raised.value)


def test_a_ceiling_stops_before_it_is_exceeded_not_after() -> None:
    # The whole point: a run with $0.10 left and a $0.30 page must not start.
    budget = clone_cost.Budget(0.10)
    budget.spend.cost = 0.10
    assert budget.remaining == 0.0
    assert not budget.can_afford("claude-opus-4", 1)


def test_remaining_is_not_reported_as_a_number_when_there_is_no_ceiling() -> None:
    budget = clone_cost.Budget()
    assert budget.to_json()["remaining"] is None
    assert budget.to_json()["ceilingText"] == "none"


def test_a_cheaper_model_fits_where_an_expensive_one_would_not() -> None:
    budget = clone_cost.Budget(0.20)

    # This is what routing buys: the run stops, not the whole feature.
    assert not budget.can_afford("claude-opus-4", 1)
    assert budget.can_afford("gpt-4o-mini", 1)


# --- money as the user sees it ----------------------------------------------


@pytest.mark.parametrize(
    ("amount", "shown"),
    [(0.0, "$0.0¢"), (0.004, "$0.4¢"), (0.02, "$0.02"), (1.5, "$1.50")],
)
def test_money_is_shown_in_units_worth_reading(amount: float, shown: str) -> None:
    assert clone_cost.describe_money(amount) == shown


# --- the ceiling in a real page generation ----------------------------------


def make_run(paths: Optional[List[str]] = None) -> CloneRun:
    run = CloneRun(run_id="r1", base_url="https://shop.test", stack="html_tailwind")
    run.crawl = CrawlResult(
        base_url="https://shop.test",
        pages=[
            CrawlPage(
                url=f"https://shop.test{path}",
                path=path,
                title=path,
                html="<html><body>hi</body></html>",
            )
            for path in (paths or ["/about"])
        ],
    )
    return run


def cfg(model: str = "claude-opus-4") -> LlmConfig:
    return LlmConfig(provider="openai", model=model, api_key="k")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache of its own.

    Without this these tests read the developer's real cache and pass for a
    reason that has nothing to do with the ceiling - a page found there costs
    nothing and looks exactly like a run that was stopped.
    """
    monkeypatch.setattr(clone_pages.clone_cache, "CACHE_DIR", tmp_path / "page-cache")


@pytest.fixture()
def counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """A model that answers, and counts how many times it was asked."""
    calls: list[int] = []

    async def fake_complete(
        _cfg: LlmConfig,
        _prompt: str,
        _turn: str,
        images: Optional[List[object]] = None,
    ) -> Completion:
        calls.append(1)
        return Completion(
            text="<!DOCTYPE html><html><head><title>x</title></head><body>h</body></html>",
            usage={"prompt_tokens": 4_000, "completion_tokens": 8_000},
        )

    monkeypatch.setattr(clone_pages, "complete", fake_complete)
    return calls


async def test_a_page_generated_under_a_ceiling_is_counted(counted: list[int]) -> None:
    budget = clone_cost.Budget(50.0)

    code, error = await clone_pages.generate_page(
        make_run(), "/about", cfg(), "http://m", budget=budget
    )

    assert code and not error
    assert budget.spend.calls == 1
    assert budget.spend.cost > 0


async def test_a_run_that_cannot_afford_the_next_page_never_asks(counted: list[int]) -> None:
    budget = clone_cost.Budget(0.01)

    code, error = await clone_pages.generate_page(
        make_run(), "/about", cfg(), "http://m", budget=budget
    )

    # The call never happens. Money checked after the fact is not a limit.
    assert counted == []
    assert code == ""
    assert "limit" in error


async def test_a_page_with_no_budget_still_generates(counted: list[int]) -> None:
    code, error = await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    assert code and not error


async def test_spending_up_to_the_ceiling_then_stopping(
    counted: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # gpt-4o at these token counts costs about nine cents a page, so a ten
    # cent ceiling lets the first page through and stops the second. A second
    # page is needed because the first one is in the cache by then, and a
    # cached page costs nothing at any ceiling.
    run = make_run(["/about", "/contact"])
    budget = clone_cost.Budget(0.10)
    cheap = cfg("gpt-4o")

    first, first_error = await clone_pages.generate_page(
        run, "/about", cheap, "http://m", budget=budget
    )
    second, second_error = await clone_pages.generate_page(
        run, "/contact", cheap, "http://m", budget=budget
    )

    assert first and not first_error
    assert second == ""
    assert "limit" in second_error
    assert len(counted) == 1


async def test_a_page_that_is_cached_is_served_past_the_ceiling(
    counted: list[int],
) -> None:
    # A page that already costs nothing is worth handing over whatever the
    # budget says: refusing it would be refusing a free answer.
    budget = clone_cost.Budget(0.10)
    cheap = cfg("gpt-4o")

    await clone_pages.generate_page(make_run(), "/about", cheap, "http://m", budget=budget)
    budget.spend.cost = budget.ceiling  # blown, by hand
    code, error = await clone_pages.generate_page(
        make_run(), "/about", cheap, "http://m", budget=budget
    )

    assert code and not error
    assert len(counted) == 1
    assert budget.spend.calls == 1


# --- the ceiling across a whole run -----------------------------------------


async def test_a_run_under_a_ceiling_keeps_what_it_finished(
    counted: list[int],
) -> None:
    import clone_runner
    import clone_runs

    run = make_run(["/a", "/b", "/c", "/d"])
    budget = clone_cost.Budget(0.10)
    cheap = cfg("gpt-4o")

    async def emit(event: Any) -> None:
        return None

    await clone_runner.generate_pages(run, cheap, "http://m", emit, spend=budget)

    done = [p for p, record in run.pages.items() if record.code]
    # Whatever happened, what was generated is kept and recorded. The user
    # is out of money, not out of work, and a resume picks the rest up.
    assert done
    assert len(done) < 4
    assert all(run.pages[path].status == clone_runs.COMPLETE for path in done)
    assert clone_runner.pages_to_generate(run)


async def test_a_run_that_hit_its_ceiling_says_it_was_the_money(
    counted: list[int],
) -> None:
    import clone_runner

    run = make_run(["/a", "/b", "/c", "/d"])
    budget = clone_cost.Budget(0.10)
    cheap = cfg("gpt-4o")

    async def emit(event: Any) -> None:
        return None

    await clone_runner.generate_pages(run, cheap, "http://m", emit, spend=budget)

    # "3 pages could not be generated" would send the user hunting a crawler
    # bug instead of telling them the bill arrived.
    assert run.phase == "partial"
    assert "spending limit" in run.error
    assert "could not be generated" not in run.error


async def test_a_run_without_a_ceiling_generates_everything(counted: list[int]) -> None:
    import clone_runner

    run = make_run(["/a", "/b", "/c"])
    cheap = cfg("gpt-4o")

    async def emit(event: Any) -> None:
        return None

    await clone_runner.generate_pages(run, cheap, "http://m", emit)

    assert len(counted) == 3
    assert all(record.code for record in run.pages.values())
