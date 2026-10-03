import asyncio
from typing import Any, Dict, List, Optional

import pytest

import clone_jobs
import clone_pages
import clone_runs
import clone_runner
from clone_jobs import Event, Registry
from clone_runs import ClonePage, CloneRun
from crawler.crawler import CrawlPage, CrawlResult


def page(path: str) -> CrawlPage:
    return CrawlPage(
        url=f"https://shop.test{path}",
        path=path,
        title=f"Page {path}",
        html="<html><body><h1>Page</h1></body></html>",
    )


def run_with(paths: List[str], done: Optional[List[str]] = None) -> CloneRun:
    run = CloneRun(run_id="r1", base_url="https://shop.test", stack="html_tailwind")
    run.crawl = CrawlResult(base_url="https://shop.test", pages=[page(p) for p in paths])
    for path in done or []:
        record = run.page(path)
        record.code = f"<html>{path}</html>"
        record.status = clone_runs.COMPLETE
    return run


def cfg() -> Any:
    return clone_runner.LlmConfig(provider="openai", model="gpt-4o", api_key="k")


# --- what is left to do -----------------------------------------------------


def test_a_fresh_run_has_every_page_to_do():
    run = run_with(["/", "/about", "/contact"])

    assert clone_runner.pages_to_generate(run) == ["/", "/about", "/contact"]


def test_a_page_that_is_already_written_is_not_done_again():
    # The whole of resume: a page with code is finished, whatever else the
    # run says about it. Redoing it costs money and can change its look.
    run = run_with(["/", "/about"], done=["/about"])

    assert clone_runner.pages_to_generate(run) == ["/"]


def test_a_page_that_failed_counts_as_not_done():
    run = run_with(["/", "/about"])
    run.page("/about").status = clone_runs.FAILED

    assert clone_runner.pages_to_generate(run) == ["/", "/about"]


def test_the_pending_count_can_be_capped():
    run = run_with([f"/p{i}" for i in range(10)])

    assert len(clone_runner.pages_to_generate(run, limit=3)) == 3


def test_a_run_with_no_crawl_has_nothing_to_do():
    run = CloneRun(run_id="r1", base_url="https://shop.test", stack="html_tailwind")

    assert clone_runner.pages_to_generate(run) == []


# --- the status line ---------------------------------------------------------


def test_one_page_is_not_called_one_pages() -> None:
    # The line the user reads while waiting; "1 pages" is the first thing
    # anyone would notice about it.
    assert clone_runner.generating_status(1, 3) == "Generating 1 page..."


def test_a_parallel_figure_is_not_claimed_when_nothing_runs_in_parallel() -> None:
    # "Generating 1 page (3 at a time)" reads as though three pages were
    # involved and sends people looking for a setting that does not exist.
    assert "at a time" not in clone_runner.generating_status(3, 3)
    assert "at a time" not in clone_runner.generating_status(2, 3)


def test_the_parallel_figure_appears_once_it_is_true() -> None:
    assert clone_runner.generating_status(10, 3) == "Generating 10 pages, 3 at a time..."


def test_many_pages_still_agree_with_the_number() -> None:
    assert "pages" in clone_runner.generating_status(2, 3)
    assert "pages" in clone_runner.generating_status(9, 3)


def test_a_run_of_one_page_still_says_it_is_working() -> None:
    # It has to remain a "we are doing something" signal, not just a count.
    assert clone_runner.generating_status(1, 3).endswith("...")


# --- generating -------------------------------------------------------------


async def test_a_page_is_written_down_and_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_with(["/"])
    seen: List[Event] = []

    async def fake_generate_page(
        target: CloneRun, path: str, config: Any, media_base_url: str, *args: Any, **kwargs: Any
    ) -> tuple[str, str]:
        return "<html>generated</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda run: None)

    async def emit(event: Event) -> None:
        seen.append(event)

    await clone_runner.generate_pages(run, cfg(), "http://m", emit)

    assert run.page("/").code == "<html>generated</html>"
    assert run.page("/").status == clone_runs.COMPLETE
    assert any(event.type == "pageComplete" for event in seen)


async def test_a_run_with_nothing_left_says_so_rather_than_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/"], done=["/"])
    seen: List[Event] = []

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("should not have called the model")

    monkeypatch.setattr(clone_pages, "generate_page", explode)
    seen_events: List[Event] = []

    async def emit(event: Event) -> None:
        seen_events.append(event)

    await clone_runner.generate_pages(run, cfg(), "http://m", emit)

    assert seen_events[0].value == "Nothing left to generate."


async def test_a_page_that_fails_is_recorded_rather_than_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/"])

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        return "", "the model returned nothing"

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda run: None)

    async def emit(event: Event) -> None:
        return None

    await clone_runner.generate_pages(run, cfg(), "http://m", emit, retry_budget=0)

    assert run.page("/").status == clone_runs.FAILED
    assert "returned nothing" in run.page("/").error


async def test_a_useless_answer_is_tried_again_within_the_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/"])
    calls = 0

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "", "commentary instead of a page"
        return "<html>second try</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda run: None)

    async def emit(event: Event) -> None:
        return None

    await clone_runner.generate_pages(run, cfg(), "http://m", emit, retry_budget=2)

    assert calls == 2
    assert run.page("/").code == "<html>second try</html>"


async def test_the_retry_allowance_is_shared_by_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every page failing should not mean three tries each: that is nine calls
    # for nothing, against a model that is already refusing.
    run = run_with(["/a", "/b", "/c"])
    calls = 0

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        return "", "no"

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda run: None)

    async def emit(event: Event) -> None:
        return None

    await clone_runner.generate_pages(run, cfg(), "http://m", emit, retry_budget=2)

    assert calls == 5  # one per page, plus two retries between them


async def test_a_run_with_no_model_says_so_instead_of_pretending_to_work() -> None:
    run = run_with(["/"])
    seen: List[Event] = []

    async def emit(event: Event) -> None:
        seen.append(event)

    await clone_runner.generate_pages(
        run, clone_runner.LlmConfig(provider="openai", model="m", api_key=""), "http://m", emit
    )

    assert seen[0].type == "error"
    assert run.page("/").code == ""


async def test_every_page_is_written_down_before_the_run_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A run store that only updates when a message goes out loses every page
    # finished while the connection was down.
    run = run_with(["/a", "/b", "/c"])
    saves = 0

    async def fake_generate_page(
        target: CloneRun, path: str, *args: Any, **kwargs: Any
    ) -> tuple[str, str]:
        return f"<html>{path}</html>", ""

    def counting_save(target: CloneRun) -> None:
        nonlocal saves
        saves += 1

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", counting_save)

    async def emit(event: Event) -> None:
        return None

    await clone_runner.generate_pages(run, cfg(), "http://m", emit)

    assert saves >= 3
    assert all(page.code for page in run.pages.values())


async def test_a_page_that_cannot_be_written_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A full disk is worth saying out loud and is not worth throwing the run
    # over: the pages are still in memory and still answerable.
    run = run_with(["/a", "/b"])

    async def fake_generate_page(
        target: CloneRun, path: str, *args: Any, **kwargs: Any
    ) -> tuple[str, str]:
        return f"<html>{path}</html>", ""

    def full_disk(target: CloneRun) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", full_disk)

    async def emit(event: Event) -> None:
        return None

    await clone_runner.generate_pages(run, cfg(), "http://m", emit)

    assert all(page.code for page in run.pages.values())


# --- as a background job ----------------------------------------------------


async def test_generation_runs_in_the_background_and_ends_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    run = run_with(["/"])

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        return "<html>done</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)

    subscriber = registry.subscribe("r1")
    clone_runner.start_generation(run, cfg(), "http://m", registry)

    for _ in range(50):
        await asyncio.sleep(0.01)
        if not job_of_running(registry):
            break

    assert run.phase == "done"
    assert not job_of_running(registry)


async def test_the_run_is_handed_over_when_the_generation_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    run = run_with(["/"])

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        return "<html>done</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)

    subscriber = registry.subscribe("r1")
    clone_runner.start_generation(run, cfg(), "http://m", registry)

    events: List[Event] = []
    for _ in range(60):
        try:
            event = await asyncio.wait_for(subscriber.queue.get(), timeout=0.05)
        except asyncio.TimeoutError:
            continue
        if event is None:
            break
        events.append(event)

    final = next((event for event in events if event.type == "setCode"), None)
    assert final is not None
    assert final.data is not None
    assert final.data["code"]["/"] == "<html>done</html>"


async def test_a_run_with_a_failed_page_is_reported_as_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    run = run_with(["/"])

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        return "", "nothing usable"

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)

    clone_runner.start_generation(run, cfg(), "http://m", registry)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not job_of_running(registry):
            break

    assert run.phase == "partial"
    assert "could not be generated" in run.error


async def test_a_second_start_does_not_pay_for_the_pages_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    run = run_with(["/"])
    calls = 0
    release = asyncio.Event()

    async def fake_generate_page(*args: Any, **kwargs: Any) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        await release.wait()
        return "<html>done</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)

    clone_runner.start_generation(run, cfg(), "http://m", registry)
    await asyncio.sleep(0.01)
    clone_runner.start_generation(run, cfg(), "http://m", registry)
    await asyncio.sleep(0.01)

    assert calls == 1
    release.set()
    await asyncio.sleep(0.05)


def job_of_running(registry: Registry) -> bool:
    job = registry.get("r1")
    return bool(job and job.running)


# --- resuming ---------------------------------------------------------------


def test_resuming_carries_on_from_what_is_already_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    run = run_with(["/a", "/b", "/c"], done=["/a", "/b"])
    generated: List[str] = []
    release = asyncio.Event()
    loop = asyncio.new_event_loop()

    async def fake_generate_page(
        target: CloneRun, path: str, *args: Any, **kwargs: Any
    ) -> tuple[str, str]:
        generated.append(path)
        return f"<html>{path}</html>", ""

    monkeypatch.setattr(clone_pages, "generate_page", fake_generate_page)
    monkeypatch.setattr(clone_runs, "save_run", lambda target: None)
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    def on_loop() -> None:
        clone_runner.resume("r1", cfg(), "http://m", registry)

    # A resume is a background start, so it needs a running loop.
    async def scenario() -> None:
        status = clone_runner.resume("r1", cfg(), "http://m", registry)
        assert status["running"] is True
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not job_of_running(registry):
                break

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()

    assert generated == ["/c"]
    assert run.page("/a").code == "<html>/a</html>"
    assert run.page("/c").code == "<html>/c</html>"


def test_resuming_a_finished_run_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/a"], done=["/a"])
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    loop = asyncio.new_event_loop()
    try:
        status = loop.run_until_complete(
            _resume_status(run, clone_runner.Registry())
        )
    finally:
        loop.close()

    assert status["pending"] == 0
    assert status["running"] is False


async def _resume_status(run: CloneRun, registry: Registry) -> Dict[str, Any]:
    return clone_runner.resume(run.run_id, cfg(), "http://m", registry)


def test_resuming_a_run_this_server_does_not_have_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: None)

    loop = asyncio.new_event_loop()
    try:
        status = loop.run_until_complete(
            _resume_status_missing(clone_runner.Registry())
        )
    finally:
        loop.close()

    assert "not on this server" in status["error"]


async def _resume_status_missing(registry: Registry) -> Dict[str, Any]:
    return clone_runner.resume("gone", cfg(), "http://m", registry)


def test_resuming_a_run_with_no_crawl_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    run = CloneRun(run_id="r1", base_url="https://x.test", stack="html_tailwind")
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    loop = asyncio.new_event_loop()
    try:
        status = loop.run_until_complete(_resume_status(run, clone_runner.Registry()))
    finally:
        loop.close()

    assert "no stored crawl" in status["error"]


# --- status -----------------------------------------------------------------


def test_status_counts_what_the_run_has_and_what_it_still_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/a", "/b", "/c"], done=["/a"])
    run.page("/b").status = clone_runs.FAILED
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    status = clone_runner.status("r1", Registry())

    assert status["known"] is True
    assert status["pagesDone"] == 1
    assert status["pagesFailed"] == 1
    assert status["pagesPending"] == 2
    assert status["running"] is False


def test_the_status_of_a_run_nobody_started_is_still_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_with(["/a"])
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: run)

    assert clone_runner.status("r1", Registry())["known"] is True


def test_the_status_of_a_run_this_server_does_not_have_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clone_runs, "load_run", lambda run_id: None)

    status = clone_runner.status("gone", Registry())

    assert status["known"] is False
    assert status["running"] is False
