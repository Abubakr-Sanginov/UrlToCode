"""What a page's own router did while it was loading.

The rule under test is that the recorder is actually read. Calling
Playwright's `evaluate` without awaiting it leaves a coroutine nobody runs:
the record comes back empty, the client-side routes are never discovered,
and the only sign is a RuntimeWarning printed once per page - which is
exactly how this stayed broken and looked like a site with no router.
"""

import asyncio
import json
from typing import Any, Dict, List

import pytest

from crawler import route_recorder


class FakePage:
    """A page whose evaluate returns the recorded routes."""

    def __init__(self, record: Any = None) -> None:
        self.record = record
        self.awaited = 0

    async def evaluate(self, _script: str) -> Any:
        self.awaited += 1
        return self.record


def test_routes_recorded_by_the_page_are_read_back() -> None:
    page = FakePage(json.dumps({"pushes": ["/a", "/b"], "requests": ["/api/x"]}))

    recorded = asyncio.run(route_recorder.read_route_recorder(page))

    assert recorded["pushes"] == ["/a", "/b"]
    assert recorded["requests"] == ["/api/x"]


def test_the_page_is_actually_asked() -> None:
    # The bug: a coroutine built and dropped reads the same as an empty
    # result, so this is the assertion that would have caught it.
    page = FakePage(json.dumps({"pushes": ["/a"], "requests": []}))

    asyncio.run(route_recorder.read_route_recorder(page))

    assert page.awaited == 1


def test_a_page_that_records_nothing_reads_as_empty() -> None:
    recorded = asyncio.run(route_recorder.read_route_recorder(FakePage(None)))

    assert recorded == {"pushes": [], "requests": []}


def test_a_page_with_no_recorder_at_all_reads_as_empty() -> None:
    # Most pages never push a route, and that is the common case.
    recorded = asyncio.run(route_recorder.read_route_recorder(FakePage("null")))

    assert recorded["pushes"] == []


def test_a_page_that_is_gone_reads_as_empty() -> None:
    class Closed:
        async def evaluate(self, _script: str) -> Any:
            raise RuntimeError("Target page, context or browser has been closed")

    recorded = asyncio.run(route_recorder.read_route_recorder(Closed()))

    assert recorded == {"pushes": [], "requests": []}


def test_something_that_is_not_a_record_reads_as_empty() -> None:
    recorded = asyncio.run(route_recorder.read_route_recorder(FakePage('"a string"')))

    assert recorded == {"pushes": [], "requests": []}


def test_entries_that_are_not_routes_are_dropped() -> None:
    page = FakePage(json.dumps({"pushes": ["/a", 7, None, "/b"], "requests": {}}))

    recorded = asyncio.run(route_recorder.read_route_recorder(page))

    assert recorded["pushes"] == ["/a", "/b"]
    assert recorded["requests"] == []
