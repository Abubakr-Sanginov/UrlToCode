"""Choosing which pages to build, before any of them are paid for.

Exercised through the real websocket handler with a fake browser connection,
so the question, the answer and the narrowing that follows are all in the
path. What matters is that the pages the user did not pick are not generated
and not charged for, and that a client that never answers does not get the
whole site generated on its behalf.
"""

import asyncio
from typing import Any, Coroutine, Dict, List, Set, cast

import pytest

from crawler.crawler import CrawlPage
from routes import url_to_code


def pages() -> List[CrawlPage]:
    return [
        CrawlPage(url=f"https://shop.test{p}", path=p, title=f"Page {p}", html="<h1>x</h1>")
        for p in ("/", "/about", "/blog/post-1", "/login")
    ]


class FakeSocket:
    """A websocket the test can talk to, with no server behind it."""

    def __init__(self, answers: List[Any], url: str = "ws://backend.test/url-to-code") -> None:
        self.sent: List[Dict[str, Any]] = []
        self.closed = False
        self.accepted = False
        self.url = _Url(url)
        self._inbox: asyncio.Queue[Any] = asyncio.Queue()
        for answer in answers:
            self._inbox.put_nowait(answer)

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> Any:
        return await self._inbox.get()

    async def send_json(self, payload: Dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    def of_type(self, name: str) -> List[Dict[str, Any]]:
        return [message for message in self.sent if message.get("type") == name]


class _Url:
    def __init__(self, value: str) -> None:
        self.scheme = value.split("://")[0]
        self.netloc = value.split("://")[1]


def ask(socket: FakeSocket) -> Coroutine[Any, Any, Set[str] | None]:
    """Ask through the real function, with the test's own socket.

    The cast is the price of not running a server: `FakeSocket` is a
    WebSocket in every way the question depends on - it is sent messages and
    answers them - and the alternative is a live browser in the test suite.
    """
    return url_to_code._await_page_selection(cast(Any, socket), pages())


# --- the question ------------------------------------------------------------


async def start_asking(socket: FakeSocket) -> "asyncio.Task[Any]":
    """Begin the question and let it reach the socket before answering."""
    task = asyncio.create_task(ask(socket))
    await asyncio.sleep(0)
    return task


async def test_the_question_is_asked_before_anything_is_generated():
    # The listing is what the user picks from, so a page missing from it is a
    # page they cannot have built.
    socket = FakeSocket([{"type": "pageSelection", "paths": ["/"]}])

    task = await start_asking(socket)
    assert len(socket.of_type("pageSelection")) == 1
    assert socket.closed is False
    await task


async def test_the_listing_carries_what_a_list_needs():
    socket = FakeSocket([{"type": "pageSelection", "paths": ["/"]}])

    task = await start_asking(socket)
    listing = socket.of_type("pageSelection")[0]["data"]["pages"]
    assert [entry["path"] for entry in listing] == ["/", "/about", "/blog/post-1", "/login"]
    assert listing[0]["title"] == "Page /"
    assert listing[0]["url"] == "https://shop.test/"
    await task


async def test_the_question_says_how_long_the_server_will_wait():
    socket = FakeSocket([{"type": "pageSelection", "paths": ["/"]}])

    task = await start_asking(socket)
    data = socket.of_type("pageSelection")[0]["data"]
    assert data["timeoutSeconds"] == url_to_code.PAGE_SELECTION_TIMEOUT_SECONDS
    await task


# --- the answer -------------------------------------------------------------


async def test_the_pages_the_user_picked_come_back():
    socket = FakeSocket([{"type": "pageSelection", "paths": ["/", "/about"]}])

    chosen = await ask(socket)

    assert chosen == {"/", "/about"}


async def test_picking_nothing_means_nothing_not_a_timeout():
    # An empty list is the user looking at the list and choosing none of it,
    # which is different from never getting an answer.
    socket = FakeSocket([{"type": "pageSelection", "paths": []}])

    assert await ask(socket) == set()


async def test_a_page_nobody_crawled_cannot_be_picked():
    socket = FakeSocket([{"type": "pageSelection", "paths": ["/", "/admin/secret"]}])

    chosen = await ask(socket)

    # Otherwise a client naming one page that does not exist would produce a
    # run of nothing, with no explanation anywhere.
    assert chosen == {"/"}


async def test_an_answer_of_the_wrong_shape_is_not_taken_as_yes():
    socket = FakeSocket([{"type": "pageSelection", "paths": "/about"}])

    assert await ask(socket) is None


async def test_waiting_too_long_gives_up_rather_than_holding_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real wait is ten minutes; what matters is that it ends, and that it
    # ends as "no answer" rather than as a run nobody approved.
    monkeypatch.setattr(url_to_code, "PAGE_SELECTION_TIMEOUT_SECONDS", 0.01)
    socket = FakeSocket([])

    assert await ask(socket) is None
