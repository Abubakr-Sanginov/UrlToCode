"""Generating a run's pages, in a way that can be carried on from.

A page is finished when it is written down, not when the user is told about
it. That is the whole of it: `CloneRun` holds the crawl and every page that
came back, so "resume" needs no history of what was in flight - it is the
list of pages that have no code yet. A run that stopped after seven of twelve
pages does seven of them again, not twelve.

Kept out of the websocket handler so the work has an owner other than the
connection. A closed tab, a dropped phone signal or a browser that put the
page to sleep stops the stream of messages and nothing else.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import clone_cost
import clone_pages
import clone_runs
from clone_jobs import Event, Emit, Registry, registry as default_registry
from clone_runs import CloneRun
from llm_http import LlmConfig

# Pages are independent calls. Three at a time keeps a ten-page site from
# taking twenty minutes without tripping a free tier's rate limit.
PAGE_CONCURRENCY = 3

# A page that came back unusable gets another go, but the allowance is shared
# by the whole run: every page failing should not mean three tries each.
DEFAULT_RETRY_BUDGET = 3

# The message a page carries when the run stopped because the user said to
# stop, rather than because the page could not be generated. Retrying is
# pointless here, and the run's own status says why.
BUDGET_STOP_MARKER = "limit in Settings"


def _is_budget_stop(error: str) -> bool:
    return BUDGET_STOP_MARKER in (error or "")

@dataclass
class RetryBudget:
    """How many more calls this run is allowed to make."""

    total: int
    used: int = 0

    def take(self) -> bool:
        if self.used >= self.total:
            return False
        self.used += 1
        return True

    @property
    def left(self) -> int:
        return max(0, self.total - self.used)


def pages_to_generate(run: CloneRun, limit: Optional[int] = None) -> List[str]:
    """The pages of this run that still have no code.

    This is the resume rule, and it is deliberately simple: a page with code
    is done, whatever its status says. A page whose generation failed can be
    asked for again by retrying it directly, which is a decision the user
    makes rather than one a resume makes for them.
    """
    if run.crawl is None:
        return []
    pending = [
        page.path
        for page in run.crawl.pages
        if not (run.pages.get(page.path) and run.pages[page.path].code)
    ]
    return pending[:limit] if limit is not None else pending


def generating_status(total: int, width: int) -> str:
    """What the status line says while pages are being built.

    The parallel figure is only worth mentioning when it is actually
    happening. "Generating 1 page (3 at a time)" reads as though three
    pages were involved, and sends people looking for a setting that does
    not exist.
    """
    pages = "page" if total == 1 else "pages"
    if total <= width:
        return f"Generating {total} {pages}..."
    return f"Generating {total} {pages}, {width} at a time..."


async def generate_pages(
    run: CloneRun,
    cfg: LlmConfig,
    media_base_url: str,
    emit: Emit,
    concurrency: int = PAGE_CONCURRENCY,
    retry_budget: int = DEFAULT_RETRY_BUDGET,
    only: Optional[List[str]] = None,
    spend_ceiling: float = 0.0,
    spend: Optional[clone_cost.Budget] = None,
) -> Dict[str, str]:
    """Generate the run's outstanding pages, writing each as it finishes.

    Returns the code map the run now holds. `emit` is called with the same
    message types the websocket already sends, so a live connection and a
    late one see the same thing.

    `spend_ceiling` is a hard limit in dollars for this run, checked before
    each call rather than after the last one. Zero means no limit, which is
    what an unset field arrives as. A caller that already has a `spend` -
    one that survived from an earlier part of the run - passes it in rather
    than starting the meter again.
    """
    paths = only if only is not None else pages_to_generate(run)
    if not paths:
        await emit(Event("status", "Nothing left to generate."))
        return run.code_map()

    if not cfg.is_usable:
        await emit(Event("error", "No model provider configured."))
        return run.code_map()

    crawl = run.crawl
    assert crawl is not None  # pages_to_generate already checked
    stack = run.stack
    generate_database = bool(run.params.get("generateDatabase"))
    link_map = clone_pages.link_map_for(crawl, stack)
    site_head = clone_pages.site_head_for(crawl)
    localize = clone_pages.media_mapper_for(crawl, media_base_url)
    budget = RetryBudget(total=max(0, retry_budget))
    # A ceiling for this run, checked before each call. Zero means no
    # ceiling, which is what an unset field arrives as.
    spend = spend if spend is not None else clone_cost.Budget(spend_ceiling)
    width = max(1, concurrency)
    slots = asyncio.Semaphore(width)

    total = len(paths)
    await emit(Event("status", generating_status(total, width)))

    async def generate(index: int, path: str) -> None:
        async with slots:
            page = next((p for p in crawl.pages if p.path == path), None)
            if page is None:
                return
            record = run.page(path)
            record.attempts += 1
            try:
                page_data = clone_pages.page_data_of(page)
                await emit(
                    Event("pageStart", f"Generating {path}", {"path": path, "pageIndex": index})
                )
                code, error = "", ""
                while True:
                    code, error = await clone_pages.generate_page(
                        run, path, cfg, media_base_url, budget=spend
                    )
                    if code:
                        break
                    if _is_budget_stop(error):
                        # The ceiling, not the page, is the reason. Retrying
                        # would only spend money the user said not to spend.
                        break
                    if not budget.take():
                        break
                    await emit(
                        Event(
                            "status",
                            f"{path}: {error or 'no usable answer'}, retry "
                            f"{budget.used}/{budget.total}",
                            {"path": path},
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One page failing is not the run failing: the others are
                # independent and already paid for.
                record.status = clone_runs.FAILED
                record.error = str(exc)
                await emit(
                    Event(
                        "pageComplete",
                        f"Failed: {path}",
                        {
                            "path": path,
                            "pageIndex": index,
                            "totalPages": total,
                            "error": True,
                            "reason": record.error,
                        },
                    )
                )
                await _checkpoint(run)
                return

            if code:
                record.status = clone_runs.COMPLETE
                record.code = code
                record.error = ""
                await emit(
                    Event(
                        "pageComplete",
                        f"Completed: {path}",
                        {
                            "path": path,
                            "pageIndex": index,
                            "totalPages": total,
                            "codeLength": len(code),
                        },
                    )
                )
            else:
                record.status = clone_runs.FAILED
                record.error = error or "no usable answer"
                await emit(
                    Event(
                        "pageComplete",
                        f"Failed: {path}",
                        {
                            "path": path,
                            "pageIndex": index,
                            "totalPages": total,
                            "error": True,
                            "reason": record.error,
                        },
                    )
                )
            # Written down before anyone is told: a run store that only
            # updates when a message goes out is a run store that loses
            # every page finished while the connection was down.
            await _checkpoint(run)

    tasks = [
        asyncio.create_task(generate(index, path)) for index, path in enumerate(paths)
    ]
    try:
        # Every page is its own task, so one refusing must not cancel the
        # rest; a page that raised has already recorded itself as failed.
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    # A run that ran out of money is not a run whose pages failed. Saying so
    # matters: "3 pages could not be generated" sends the user off to fix a
    # broken crawler instead of telling them the bill arrived.
    if _is_budget_stop(run.error) or any(
        _is_budget_stop(record.error) for record in run.pages.values()
    ):
        spent = clone_cost.describe_money(spend.spend.cost)
        run.phase = "partial"
        run.error = (
            f"Stopped at the spending limit after {spent}. "
            f"Everything generated so far is saved."
        )
        await emit(Event("status", run.error, spend.to_json()))

    return run.code_map()


async def _checkpoint(run: CloneRun) -> None:
    """Persist the run, treating a full disk as a warning and not a failure.

    A page that is in memory but not on disk is a page the user pays for
    again if the process dies; that is worth saying out loud, and not worth
    throwing away the run over.
    """
    try:
        clone_runs.save_run(run)
    except OSError as exc:
        print(f"[Runner] Could not write run {run.run_id}: {exc}")


def start_generation(
    run: CloneRun,
    cfg: LlmConfig,
    media_base_url: str,
    registry: Registry = default_registry,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run this clone's pages in the background and report where it is.

    A run already being generated is left alone: two tabs on the same clone
    should watch one set of model calls, not race to make them.
    """
    existing = registry.get(run.run_id)
    if existing is not None and existing.running:
        return existing.status()

    run.phase = "generating"
    _checkpoint_sync(run)
    # Owned here so the amount survives past the pages: the run reports what
    # it spent, and a caller that wants a ceiling says so before it starts.
    spend_ceiling = float(kwargs.get("spend_ceiling") or 0.0)
    spend = clone_cost.Budget(spend_ceiling)

    async def work(emit: Emit) -> None:
        try:
            await generate_pages(
                run, cfg, media_base_url, emit, spend=spend, **kwargs
            )
            failures = [p for p in run.pages.values() if p.status == clone_runs.FAILED]
            run.phase = "done" if not failures else "partial"
            # A run that stopped for money keeps that reason: "3 pages could
            # not be generated" would send the user hunting a crawler bug.
            if not _is_budget_stop(run.error):
                run.error = (
                    f"{len(failures)} page(s) could not be generated." if failures else ""
                )
            await _checkpoint(run)
            # The bill goes out with the result. A run that finished is the
            # moment the user can see what it cost, and the only moment it
            # would be too late to stop them spending more.
            if not spend.unlimited:
                await emit(
                    Event(
                        "status",
                        f"Spent {clone_cost.describe_money(spend.spend.cost)}",
                        spend.to_json(),
                    )
                )
            await emit(
                Event(
                    "setCode",
                    f"{len(run.pages)} files ready",
                    {"code": run.code_map(), "runId": run.run_id},
                )
            )
        except Exception as exc:
            run.phase = "failed"
            run.error = str(exc)
            await _checkpoint(run)
            await emit(Event("error", str(exc)))

    return registry.start(run.run_id, work).status()


def _checkpoint_sync(run: CloneRun) -> None:
    try:
        clone_runs.save_run(run)
    except OSError as exc:
        print(f"[Runner] Could not write run {run.run_id}: {exc}")


def resume(
    run_id: str,
    cfg: LlmConfig,
    media_base_url: str,
    registry: Registry = default_registry,
) -> Dict[str, Any]:
    """Carry a run on from whatever is already done.

    The key is supplied by the caller and never stored: the run keeps the
    provider and model it was started with, so a resumed run cannot quietly
    switch to a different one and produce pages that do not match the rest.
    """
    run = clone_runs.load_run(run_id)
    if run is None:
        return {"error": "That run is not on this server any more."}
    if run.crawl is None:
        return {"error": "This run has no stored crawl to generate from."}

    pending = pages_to_generate(run)
    if not pending:
        return {
            "runId": run_id,
            "running": False,
            "pending": 0,
            "error": "",
        }
    return start_generation(run, cfg, media_base_url, registry, only=pending)


def status(run_id: str, registry: Registry = default_registry) -> Dict[str, Any]:
    """Where a run is, from both the live job and what was written down."""
    job = registry.get(run_id)
    run = clone_runs.load_run(run_id)
    if run is None:
        return {"runId": run_id, "known": False, "running": False}

    pages = run.pages
    done = sum(1 for page in pages.values() if page.code)
    failed = sum(1 for page in pages.values() if page.status == clone_runs.FAILED and not page.code)
    pending = len(pages_to_generate(run))

    return {
        "runId": run_id,
        "known": True,
        "running": bool(job and job.running),
        "phase": run.phase,
        "error": run.error,
        "pagesTotal": len(pages),
        "pagesDone": done,
        "pagesFailed": failed,
        "pagesPending": pending,
        "stack": run.stack,
        "baseUrl": run.base_url,
    }
