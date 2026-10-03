"""The console must not be able to break a generation.

The prompt preview used to draw a box with box-drawing characters. On a
Windows console defaulting to cp1251, printing one raised
UnicodeEncodeError from the middle of a generation - after the prompt was
correctly assembled, and before the model was ever called. The user saw
"Error assembling prompt" and nothing happened.

These tests put a cp1251 console in the way and do the thing that used to
fail.
"""

import sys
from contextlib import contextmanager
from typing import Iterator

from utils import print_prompt_preview


class _Cp1251Console:
    """A console that refuses anything outside cp1251, like a real one.

    Only enough of a stream for print(). A TextIOWrapper would do the job
    too, but those are finalised when collected and a collected wrapper
    closes the stream a later test is using.
    """

    def __init__(self) -> None:
        self._bytes = bytearray()

    def write(self, text: str) -> int:
        # strict is the point: this raises on anything cp1251 cannot hold.
        self._bytes.extend(text.encode("cp1251"))
        return len(text)

    def flush(self) -> None:
        pass

    def what_was_written(self) -> str:
        return self._bytes.decode("cp1251")


@contextmanager
def cp1251_console() -> Iterator[_Cp1251Console]:
    """Make the console un-encodable for the length of a test.

    Done inside the test body rather than in a fixture on purpose: pytest
    installs its own capture stream during setup, after a fixture of ours
    would have run, and it wins.
    """
    console = _Cp1251Console()
    original = sys.stdout
    sys.stdout = console
    try:
        yield console
    finally:
        sys.stdout = original


def _preview(content: str = "make me a page") -> None:
    print_prompt_preview([{"role": "user", "content": content}])


def test_a_prompt_preview_survives_a_console_that_cannot_encode_it() -> None:
    # The regression: this raised UnicodeEncodeError, which the generation
    # pipeline did not survive.
    with cp1251_console():
        _preview()


def test_the_preview_still_shows_something_when_it_draws() -> None:
    with cp1251_console() as console:
        _preview()

    # Not swallowed silently: a diagnostic that prints nothing is no use
    # when something has just gone wrong.
    assert "PROMPT PREVIEW" in console.what_was_written()
    assert "make me a page" in console.what_was_written()


def test_preview_characters_are_ascii() -> None:
    # ASCII means the preview draws on any console, whatever its codepage,
    # so it no longer depends on main.py having reconfigured the stream.
    with cp1251_console() as console:
        _preview()

    assert console.what_was_written(), "the preview drew nothing at all"
    assert console.what_was_written().isascii()


def test_non_ascii_content_does_not_stop_the_preview() -> None:
    # A crawled Chinese page is ordinary content here, and its title can
    # reach the preview.
    with cp1251_console() as console:
        _preview("复制这个网站")

    assert "PROMPT PREVIEW" in console.what_was_written()


def test_a_closed_stream_costs_the_log_line_and_nothing_more() -> None:
    class _Closed:
        def write(self, _text: str) -> int:
            raise ValueError("I/O operation on closed file")

        def flush(self) -> None:
            raise ValueError("I/O operation on closed file")

    original = sys.stdout
    sys.stdout = _Closed()
    try:
        # Must not raise: a diagnostic is never worth failing a request
        # over, least of all when the failure is the missing output.
        _preview()
    finally:
        sys.stdout = original