"""The cache as the generator sees it.

The unit tests cover the store. This covers the thing that matters: a second
clone of the same site does not call the model again, and a repair does not
read the answer the repair is meant to replace.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import clone_cache
import clone_pages
import clone_runs
from clone_runs import CloneRun
from crawler.crawler import CrawlPage, CrawlResult
from llm_http import Completion, LlmConfig


@pytest.fixture(autouse=True)
def empty_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clone_cache, "CACHE_DIR", tmp_path / "page-cache")


@pytest.fixture(autouse=True)
def no_real_completion(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[str, str]]:
    """Record the calls that were really made, and answer with usable HTML."""
    calls: List[Tuple[str, str]] = []

    async def fake_complete(
        cfg: LlmConfig,
        prompt: str,
        user_turn: str,
        images: Optional[List[object]] = None,
    ) -> Completion:
        calls.append((prompt, user_turn))
        return Completion(
            text="<!DOCTYPE html><html><head><title>x</title></head>"
            "<body><h1>generated</h1></body></html>"
        )

    monkeypatch.setattr(clone_pages, "complete", fake_complete)
    return calls


def make_run(paths: Optional[List[str]] = None) -> CloneRun:
    run = CloneRun(run_id="r1", base_url="https://shop.test", stack="html_tailwind")
    run.crawl = CrawlResult(
        base_url="https://shop.test",
        pages=[
            CrawlPage(
                url=f"https://shop.test{path}",
                path=path,
                title="Page",
                html="<html><body><h1>Original</h1></body></html>",
            )
            for path in (paths or ["/", "/about"])
        ],
    )
    run.llm = {"provider": "openai", "model": "gpt-4o"}
    return run


def cfg() -> LlmConfig:
    return LlmConfig(provider="openai", model="gpt-4o", api_key="sk-test")


# --- the second clone is free -----------------------------------------------


async def test_generating_a_page_asks_the_model_the_first_time(no_real_completion):
    code, error = await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    assert code
    assert error == ""
    assert len(no_real_completion) == 1


async def test_generating_the_same_page_again_does_not_ask_the_model(
    no_real_completion,
):
    first, _ = await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")
    second, error = await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    assert second == first
    assert error == ""
    assert len(no_real_completion) == 1


async def test_a_page_is_only_cached_for_its_own_route(no_real_completion):
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")
    await clone_pages.generate_page(make_run(), "/", cfg(), "http://m")

    assert len(no_real_completion) == 2


# --- what must not be served from the cache ---------------------------------


async def test_a_repair_does_not_read_the_page_it_is_replacing(no_real_completion):
    run = make_run()
    original, _ = await clone_pages.generate_page(run, "/about", cfg(), "http://m")

    repaired, _ = await clone_pages.generate_page(
        run,
        "/about",
        cfg(),
        "http://m",
        instruction="The header overlaps the hero. Fix the spacing.",
        existing_code=original,
    )

    # A repair exists because the cached page was wrong. Serving it back
    # would make the repair a no-op and the score unchanged.
    assert len(no_real_completion) == 2
    assert "The header overlaps the hero" in no_real_completion[1][0]
    assert repaired


async def test_a_repair_does_not_replace_what_the_cache_holds(no_real_completion):
    run = make_run()
    await clone_pages.generate_page(run, "/about", cfg(), "http://m")

    await clone_pages.generate_page(
        run,
        "/about",
        cfg(),
        "http://m",
        instruction="Make it blue.",
        existing_code="<html>old</html>",
    )

    # The good answer is still there for a plain regeneration; a repair that
    # overwrote it would mean one bad repair poisons every later retry.
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")
    assert len(no_real_completion) == 2


async def test_a_different_model_is_asked_rather_than_served_a_cached_page(
    no_real_completion,
):
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    other = LlmConfig(provider="anthropic", model="claude-3-5-sonnet", api_key="sk-test")
    await clone_pages.generate_page(make_run(), "/about", other, "http://m")

    assert len(no_real_completion) == 2


async def test_a_different_site_is_asked_rather_than_served_a_cached_page(
    no_real_completion,
):
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    elsewhere = make_run()
    elsewhere.base_url = "https://other.test"
    elsewhere.crawl = CrawlResult(
        base_url="https://other.test",
        pages=[
            CrawlPage(
                url="https://other.test/about",
                path="/about",
                title="Other",
                html="<html><body>Different site</body></html>",
            )
        ],
    )
    await clone_pages.generate_page(elsewhere, "/about", cfg(), "http://m")

    assert len(no_real_completion) == 2


async def test_a_changed_prompt_is_asked_rather_than_served_a_cached_page(
    no_real_completion, monkeypatch: pytest.MonkeyPatch
):
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    # The prompt version is what the key is built from, read at call time so
    # a bump actually takes effect rather than being frozen at import.
    monkeypatch.setattr(clone_pages, "PAGE_PROMPT_VERSION", "2")
    await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    assert len(no_real_completion) == 2


# --- a miss falls back to the model -----------------------------------------


async def test_a_page_the_model_cannot_produce_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, no_real_completion
):
    async def refuse(
        cfg: LlmConfig,
        prompt: str,
        user_turn: str,
        images: Optional[List[object]] = None,
    ) -> Completion:
        return Completion(text="I'd be happy to help, but first tell me more.")

    monkeypatch.setattr(clone_pages, "complete", refuse)

    code, error = await clone_pages.generate_page(make_run(), "/about", cfg(), "http://m")

    assert code == ""
    assert error
    # Caching this would make every later attempt return the same refusal.
    assert clone_cache.get(
        clone_cache.key_for("https://shop.test", "/about", "html_tailwind", "gpt-4o", "1", False)
    ) is None


async def test_a_run_with_no_model_is_asked_for_nothing(no_real_completion) -> None:
    unusable = LlmConfig(provider="openai", model="gpt-4o", api_key="")

    code, error = await clone_pages.generate_page(make_run(), "/about", unusable, "http://m")

    assert code == ""
    assert "provider" in error.lower()
    assert len(no_real_completion) == 0
