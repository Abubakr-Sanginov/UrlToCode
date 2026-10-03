import asyncio
from typing import Any, List

import pytest

import clone_jobs
from clone_jobs import Event, Registry


async def drain(subscriber: clone_jobs.Subscriber, limit: int = 10) -> List[Event]:
    """Read up to `limit` events, then stop."""
    seen: List[Event] = []
    for _ in range(limit):
        try:
            event = await asyncio.wait_for(subscriber.queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            break
        if event is None:
            break
        seen.append(event)
    return seen


def job_of(registry: Registry, run_id: str) -> clone_jobs.Job:
    """The run's job, which the test then reads."""
    job = registry.get(run_id)
    assert job is not None, f"{run_id} has no job"
    return job


# --- events reach a listener ------------------------------------------------


async def test_an_event_reaches_the_listener():
    registry = Registry()
    subscriber = registry.subscribe("run1")

    async def work(emit: clone_jobs.Emit) -> None:
        await emit(Event("status", "crawling"))
        await emit(Event("pageComplete", "done", {"path": "/"}))

    registry.start("run1", work)

    seen = await drain(subscriber)
    assert [event.type for event in seen] == ["status", "pageComplete"]
    assert seen[1].data == {"path": "/"}


async def test_a_run_with_no_listener_still_runs():
    # The whole point: closing the tab must not stop the work.
    registry = Registry()
    done = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await emit(Event("status", "working"))
        done.set()

    registry.start("run1", work)
    await asyncio.wait_for(done.wait(), timeout=1)

    assert registry.get("run1") is not None


async def test_every_listener_sees_every_event():
    registry = Registry()
    first = registry.subscribe("run1")
    second = registry.subscribe("run1")

    async def work(emit: clone_jobs.Emit) -> None:
        for index in range(3):
            await emit(Event("status", str(index)))

    registry.start("run1", work)

    assert len(await drain(first)) == 3
    assert len(await drain(second)) == 3


async def test_a_listener_that_joins_late_still_gets_what_follows():
    registry = Registry()
    early = registry.subscribe("run1")
    started = asyncio.Event()
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await emit(Event("status", "first"))
        started.set()
        await release.wait()
        await emit(Event("status", "last"))

    registry.start("run1", work)
    await started.wait()

    late = registry.subscribe("run1")
    release.set()

    assert [event.value for event in await drain(late)] == ["last"]


# --- the stream ends --------------------------------------------------------


async def test_the_stream_ends_when_the_run_does():
    # A listener blocked on an empty queue would otherwise wait forever.
    registry = Registry()
    subscriber = registry.subscribe("run1")

    async def work(emit: clone_jobs.Emit) -> None:
        return None

    registry.start("run1", work)

    assert await drain(subscriber) == []


async def test_a_finished_run_ends_the_stream_immediately():
    registry = Registry()
    registry.start("run1", _no_work)
    await asyncio.sleep(0)

    subscriber = registry.subscribe("run1")

    assert await drain(subscriber) == []


async def _no_work(emit: clone_jobs.Emit) -> None:
    return None


# --- one run per clone ------------------------------------------------------


async def test_a_second_start_does_not_run_the_work_again():
    # Two tabs on the same clone should watch one set of model calls, not
    # race to make them.
    registry = Registry()
    calls = 0
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    first = registry.start("run1", work)
    second = registry.start("run1", work)
    # The task does not run until the loop gets control, so a count read
    # here would see zero calls even when one was about to happen.
    await asyncio.sleep(0)

    assert first is second
    assert calls == 1
    release.set()
    await asyncio.sleep(0)


async def test_a_run_can_be_started_again_once_it_has_finished():
    registry = Registry()
    calls = 0

    async def work(emit: clone_jobs.Emit) -> None:
        nonlocal calls
        calls += 1

    registry.start("run1", work)
    await asyncio.sleep(0.01)
    registry.start("run1", work)
    await asyncio.sleep(0.01)

    assert calls == 2


# --- leaving ----------------------------------------------------------------


async def test_a_listener_that_leaves_does_not_stop_the_run():
    # This is the whole point of the module: closing the tab must not stop
    # the work, so a later listener can pick it up where it got to.
    registry = Registry()
    subscriber = registry.subscribe("run1")
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await release.wait()

    registry.start("run1", work)
    await asyncio.sleep(0)

    registry.unsubscribe("run1", subscriber)
    await asyncio.sleep(0.01)

    assert job_of(registry, "run1").running
    assert job_of(registry, "run1").subscribers == set()
    release.set()
    await asyncio.sleep(0)


async def test_a_run_a_later_listener_can_pick_up_is_not_thrown_away():
    registry = Registry()
    first = registry.subscribe("run1")
    reached = asyncio.Event()
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await emit(Event("pageComplete", "one", {"path": "/"}))
        reached.set()
        await release.wait()

    registry.start("run1", work)
    await reached.wait()
    registry.unsubscribe("run1", first)

    second = registry.subscribe("run1")
    release.set()
    await asyncio.sleep(0)

    assert await drain(second) == []


async def test_a_run_with_a_listener_left_is_not_cancelled():
    registry = Registry()
    first = registry.subscribe("run1")
    second = registry.subscribe("run1")
    finished = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            finished.set()
            raise

    registry.start("run1", work)
    await asyncio.sleep(0)

    registry.unsubscribe("run1", first)
    await asyncio.sleep(0.01)

    assert not finished.is_set()
    assert job_of(registry, "run1").running
    registry.unsubscribe("run1", second)


# --- failures ---------------------------------------------------------------


async def test_a_failure_is_reported_rather_than_raised_at_the_listener():
    registry = Registry()
    subscriber = registry.subscribe("run1")

    async def work(emit: clone_jobs.Emit) -> None:
        raise RuntimeError("the provider refused")

    registry.start("run1", work)
    await asyncio.sleep(0.01)

    job = job_of(registry, "run1")
    assert job.error == "the provider refused"
    assert not job.running
    assert await drain(subscriber) == []


# --- a listener that cannot keep up ----------------------------------------


async def test_a_listener_that_stops_reading_does_not_block_the_run():
    registry = Registry()
    subscriber = registry.subscribe("run1")
    finished = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        for index in range(clone_jobs.MAX_QUEUED_EVENTS * 3):
            await emit(Event("status", str(index)))
        finished.set()

    registry.start("run1", work)
    await asyncio.wait_for(finished.wait(), timeout=2)

    queued = subscriber.queue.qsize()
    assert queued <= clone_jobs.MAX_QUEUED_EVENTS


async def test_the_oldest_events_are_the_ones_dropped():
    # The run store already holds the finished pages, so a client that fell
    # behind catches up from there rather than from a backlog.
    registry = Registry()
    subscriber = registry.subscribe("run1")
    total = clone_jobs.MAX_QUEUED_EVENTS + 10

    async def work(emit: clone_jobs.Emit) -> None:
        for index in range(total):
            await emit(Event("status", str(index)))

    registry.start("run1", work)
    await asyncio.sleep(0.05)

    seen = await drain(subscriber, limit=clone_jobs.MAX_QUEUED_EVENTS)
    values = [event.value for event in seen]

    # The newest is the one that must survive; the oldest is what goes.
    assert values[-1] == str(total - 1)
    assert values[0] not in {"0", "1"}
    assert len(values) <= clone_jobs.MAX_QUEUED_EVENTS


async def test_the_stream_still_ends_after_dropping_events():
    # A stream that never ends leaves the client waiting for something that
    # is not coming, which is worse than losing one stale event.
    registry = Registry()
    subscriber = registry.subscribe("run1")
    finished = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        for index in range(clone_jobs.MAX_QUEUED_EVENTS * 2):
            await emit(Event("status", str(index)))
        finished.set()

    registry.start("run1", work)
    await asyncio.wait_for(finished.wait(), timeout=2)
    await asyncio.sleep(0.05)

    drained = 0
    while True:
        event = await asyncio.wait_for(subscriber.queue.get(), timeout=0.2)
        if event is None:
            break
        drained += 1
    assert drained <= clone_jobs.MAX_QUEUED_EVENTS


# --- status and cleanup -----------------------------------------------------


async def test_status_says_what_is_happening():
    registry = Registry()
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await release.wait()

    registry.start("run1", work)
    await asyncio.sleep(0)

    status = job_of(registry, "run1").status()
    assert status["running"] is True
    assert status["runId"] == "run1"
    assert status["subscribers"] == 0
    release.set()
    await asyncio.sleep(0)


async def test_a_run_can_be_stopped():
    registry = Registry()
    finished = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            finished.set()
            raise

    registry.start("run1", work)
    await asyncio.sleep(0)

    assert registry.cancel("run1") is True
    await asyncio.wait_for(finished.wait(), timeout=1)
    assert job_of(registry, "run1").error == "stopped"


async def test_stopping_a_run_that_is_not_running_says_so():
    registry = Registry()

    assert registry.cancel("nothing") is False
    registry.start("run1", _no_work)
    await asyncio.sleep(0.01)
    assert registry.cancel("run1") is False


async def test_forgetting_a_run_takes_it_off_the_list():
    registry = Registry()
    registry.start("run1", _no_work)
    await asyncio.sleep(0.01)

    registry.forget("run1")

    assert registry.get("run1") is None
    assert registry.running_ids() == []


async def test_the_list_of_runs_names_only_the_live_ones():
    registry = Registry()
    release = asyncio.Event()

    async def work(emit: clone_jobs.Emit) -> None:
        await release.wait()

    registry.start("run1", work)
    registry.start("run2", _no_work)
    await asyncio.sleep(0.01)

    assert registry.running_ids() == ["run1"]
    assert {entry["runId"] for entry in registry.statuses()} == {"run1", "run2"}
    release.set()
    await asyncio.sleep(0)


async def test_an_event_with_no_data_still_serialises():
    payload = Event("status", "hi").to_json()

    assert payload == {"type": "status", "value": "hi", "data": {}}
