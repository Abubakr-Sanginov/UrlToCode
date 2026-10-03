"""Generation that keeps going when nobody is watching.

A clone is minutes of model calls. Tied to a websocket, the work stops the
moment the socket does - the laptop lid closes, the phone loses signal, the
user opens Settings - and comes back as a run that got halfway and then
stopped, with no way to say "carry on from there" except starting over and
paying for the same pages twice.

So the work belongs to the run, not to the connection. A job owns the
generation; a websocket subscribes to it. A subscriber that goes away leaves
the job running, and coming back attaches to whatever is left rather than
restarting it. The run store is the record: every finished page is written
there before anyone is told about it, so a late subscriber reads the same
state a live one would have been given.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Set, cast

# A subscriber that stops reading must not be able to grow the queue without
# bound, and must not be able to hold up the run that is writing to it.
MAX_QUEUED_EVENTS = 200

# Written to a queue to mean "nothing more is coming". Not an Event, because
# it is a property of the stream rather than something the run did.
END_OF_STREAM: Optional[Event] = None

# One job per run. A second websocket on the same run attaches to the first.
Emit = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    """One thing the run did, in the shape the websocket already sends."""

    type: str
    value: str = ""
    data: Optional[Dict[str, Any]] = None

    def to_json(self) -> Dict[str, Any]:
        return {"type": self.type, "value": self.value, "data": self.data or {}}


@dataclass(eq=False)
class Subscriber:
    """One attached listener.

    Compared by identity, not by fields: two listeners on the same run are
    interchangeable by value, and a set that treated them as the same entry
    would silently drop one of them.
    """

    queue: "asyncio.Queue[Optional[Event]]" = field(default_factory=lambda: asyncio.Queue())

    def offer(self, event: Optional[Event]) -> None:
        if self.queue.qsize() >= MAX_QUEUED_EVENTS:
            # Drop the oldest rather than the newest or block: the run store
            # already holds the finished pages, so a client that fell behind
            # can catch up from there.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(event)

    def close(self) -> None:
        """Mark the stream finished.

        Goes through the same bound as any other event: a stream that never
        ends leaves the client waiting for something that is not coming, and
        a listener blocked forever holds a connection open for nothing. One
        stale event is the cheaper of the two.
        """
        self.offer(END_OF_STREAM)

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event


@dataclass
class Job:
    """A run's generation, and whoever is watching it."""

    run_id: str
    task: Optional["asyncio.Task[None]"] = None
    subscribers: Set[Subscriber] = field(
        default_factory=lambda: cast(Set[Subscriber], set())
    )
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    # Counters, so a status request can say something useful without walking
    # the run store.
    pages_done: int = 0
    pages_failed: int = 0

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> Dict[str, Any]:
        return {
            "runId": self.run_id,
            "running": self.running,
            "subscribers": len(self.subscribers),
            "pagesDone": self.pages_done,
            "pagesFailed": self.pages_failed,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "error": self.error,
        }

    async def emit(self, event: Event) -> None:
        for subscriber in list(self.subscribers):
            subscriber.offer(event)


class Registry:
    """Every job this process is running."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def get(self, run_id: str) -> Optional[Job]:
        return self._jobs.get(run_id)

    def _slot(self, run_id: str) -> Job:
        """The job record for a run, created on first touch.

        Created here rather than in `start` so a listener that attached
        before the work began is still attached when it does: the
        subscription is the reason the record exists.
        """
        job = self._jobs.get(run_id)
        if job is None:
            job = Job(run_id=run_id)
            self._jobs[run_id] = job
        return job

    def start(
        self,
        run_id: str,
        work: Callable[[Emit], Awaitable[None]],
    ) -> Job:
        """Run `work` in the background for `run_id`.

        A job already running for this run is returned rather than started
        again: two tabs on the same clone should watch one set of model
        calls, not race to make them.
        """
        job = self._slot(run_id)
        if job.running:
            return job

        job.started_at = time.time()
        job.finished_at = 0.0
        job.error = ""
        job.task = asyncio.create_task(self._run(job, work))
        return job

    async def _run(self, job: Job, work: Callable[[Emit], Awaitable[None]]) -> None:
        try:
            await work(job.emit)
        except asyncio.CancelledError:
            # The run store already holds every page that finished, so a
            # cancelled job is resumable rather than lost.
            job.error = "stopped"
            raise
        except Exception as exc:
            job.error = str(exc)
            print(f"[Jobs] {job.run_id} failed: {exc}")
        finally:
            job.finished_at = time.time()
            for subscriber in list(job.subscribers):
                # END_OF_STREAM is how a subscriber blocked on an empty queue
                # learns there is nothing more coming.
                subscriber.close()

    def subscribe(self, run_id: str) -> Subscriber:
        """Listen to a run.

        A run that is not being generated right now still gets a subscriber,
        so the caller can end the stream cleanly instead of blocking on a
        queue with no writer.
        """
        subscriber = Subscriber()
        job = self._slot(run_id)
        job.subscribers.add(subscriber)
        # Only a run that has already finished ends the stream on arrival. A
        # run that has not started yet is about to: the websocket attaches
        # and then asks for the work to begin, and ending the stream there
        # would leave it listening to nothing.
        if job.finished_at or job.started_at and not job.running:
            subscriber.close()
        return subscriber

    def unsubscribe(self, run_id: str, subscriber: Subscriber) -> None:
        job = self._jobs.get(run_id)
        if job is None:
            return
        job.subscribers.discard(subscriber)
        # The run itself is deliberately left going: this is a background
        # job, and a closed tab is the case it exists for. It is closed by
        # `cancel`, not by the last listener leaving.
        subscriber.close()

    def cancel(self, run_id: str) -> bool:
        """Stop a run that is still going."""
        job = self._jobs.get(run_id)
        if job is None or job.task is None or job.task.done():
            return False
        job.task.cancel()
        return True

    def forget(self, run_id: str) -> None:
        job = self._jobs.pop(run_id, None)
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()

    def running_ids(self) -> List[str]:
        return [run_id for run_id, job in self._jobs.items() if job.running]

    def statuses(self) -> List[Dict[str, Any]]:
        return [job.status() for job in self._jobs.values()]


# One registry for the process. A job belongs to the backend that started it:
# resuming after a restart reads the run store, not this.
registry = Registry()
