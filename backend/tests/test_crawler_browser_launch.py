"""Starting the browser when there is nothing to display it on.

The crawler wants a headed Chromium: bot checks flag headless and serve
"Just a moment..." instead of the site. But a headed browser needs an X
display, and a container deployed without one has none - Render's native
Python runtime being the obvious case. There the launch fails, the worker
dies, and the person gets an error instead of a clone.

The fix is to fall back rather than to stop, because a worse clone beats no
clone. What must not happen is a blanket fallback: a browser that was never
downloaded fails the same way in either mode, and quietly re-running it
headless would hide a deployment that needs `playwright install` until
somebody noticed the crawl quality instead.
"""

import asyncio
from typing import Any

import pytest

from crawler import playwright_worker as worker


class FakeChromium:
    """A browser launch that refuses in one mode and works in the other."""

    def __init__(self, refuse_when: bool) -> None:
        self.refuse_when = refuse_when
        self.launches: list = []

    async def launch(self, headless: bool, args=None):
        self.launches.append(headless)
        if headless == self.refuse_when:
            raise RuntimeError(
                "Host system is missing dependencies to run browsers: "
                "libX11.so: cannot open display"
            )
        return f"browser(headless={headless})"


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class TestNoDisplayToShowABrowserOn:
    def test_it_crawls_headless_rather_than_dying(self):
        """The whole point. A refused headed launch used to take the crawl
        with it."""
        chromium = FakeChromium(refuse_when=False)
        p = FakePlaywright(chromium)

        browser = asyncio.run(worker._launch_chromium(p, headless=False))

        assert browser == "browser(headless=True)"
        assert chromium.launches == [False, True]

    def test_a_headed_machine_is_left_alone(self):
        """The fallback is for machines that need it. Touching the working
        path would change the crawl for everyone who has a display."""
        chromium = FakeChromium(refuse_when=True)
        p = FakePlaywright(chromium)

        browser = asyncio.run(worker._launch_chromium(p, headless=False))

        assert browser == "browser(headless=False)"
        assert chromium.launches == [False]

    def test_it_says_that_it_gave_up_the_display(self, capsys):
        """Otherwise a bot check serves a "Just a moment" page and the result
        looks like a bad clone rather than a machine with no screen."""
        chromium = FakeChromium(refuse_when=False)
        p = FakePlaywright(chromium)

        asyncio.run(worker._launch_chromium(p, headless=False))

        assert "headless" in capsys.readouterr().err.lower()


class TestFailuresThatAreNotADisplay:
    def test_a_browser_that_was_never_downloaded_is_not_retried(self):
        """Same failure in both modes. Retrying headless would hide a
        deployment missing `playwright install` behind a crawl that quietly
        gets worse instead of loudly failing."""
        class Missing(FakeChromium):
            async def launch(self, headless: bool, args=None):
                self.launches.append(headless)
                raise RuntimeError(
                    "Executable doesn't exist at "
                    "/root/.cache/ms-playwright/chromium-1234/chrome-linux/chrome"
                )

        chromium = Missing(refuse_when=False)
        p = FakePlaywright(chromium)

        with pytest.raises(RuntimeError):
            asyncio.run(worker._launch_chromium(p, headless=False))

        assert chromium.launches == [False], "tried headless anyway"

    def test_headless_is_never_retried(self):
        """It already is the fallback; there is nothing to fall back to."""
        class Broken(FakeChromium):
            async def launch(self, headless: bool, args=None):
                self.launches.append(headless)
                raise RuntimeError("cannot open display")

        chromium = Broken(refuse_when=True)
        p = FakePlaywright(chromium)

        with pytest.raises(RuntimeError):
            asyncio.run(worker._launch_chromium(p, headless=True))

        assert chromium.launches == [True]


class TestNoticingThereIsNoDisplayBeforeLaunching:
    """Waiting for a headed launch to fail is waiting for the timeout.

    On Linux a Chromium with nowhere to draw can sit waiting for an X server
    that is never coming, producing no output at all. That is what a deployed
    worker with no Xvfb does, and it is why the crawl ended at the subprocess
    timeout looking like a hang rather than reporting anything.
    """

    def test_linux_without_display_says_so(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker.sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)

        assert worker._no_display_here() is True

    def test_linux_with_a_display_is_left_headed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker.sys, "platform", "linux")
        monkeypatch.setenv("DISPLAY", ":99")

        assert worker._no_display_here() is False

    @pytest.mark.parametrize("platform", ["win32", "darwin"])
    def test_other_platforms_are_never_affected(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ):
        """DISPLAY is an X11 thing. Windows and macOS have screens without it,
        so its absence must not be read as having no display."""
        monkeypatch.setattr(worker.sys, "platform", platform)
        monkeypatch.delenv("DISPLAY", raising=False)

        assert worker._no_display_here() is False

    def test_it_does_not_ask_for_a_window_it_cannot_open(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        monkeypatch.setattr(worker.sys, "platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        # Refuses a window and accepts headless, so a headed attempt would
        # show up as a second launch rather than as success.
        chromium = FakeChromium(refuse_when=False)
        p = FakePlaywright(chromium)

        browser = asyncio.run(worker._launch_chromium(p, headless=False))

        assert browser == "browser(headless=True)"
        assert chromium.launches == [True], "it tried headed anyway"
        assert "DISPLAY" in capsys.readouterr().err

    def test_a_machine_with_a_display_still_gets_a_window(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The whole reason for headed: bot checks serve the check page to a
        headless browser. Deciding this wrongly makes every clone worse."""
        monkeypatch.setattr(worker.sys, "platform", "linux")
        monkeypatch.setenv("DISPLAY", ":99")
        chromium = FakeChromium(refuse_when=True)
        p = FakePlaywright(chromium)

        browser = asyncio.run(worker._launch_chromium(p, headless=False))

        assert browser == "browser(headless=False)"
        assert chromium.launches == [False]


class TestTellingTheTwoApart:
    @pytest.mark.parametrize(
        "message",
        [
            "Missing X server or $DISPLAY",
            "cannot open display: :0",
            "Xvfb failed to start",
            "Gtk-WARNING: cannot open display",
        ],
    )
    def test_a_display_problem_is_recognised(self, message: str):
        assert worker._looks_like_no_display(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "Executable doesn't exist at /root/.cache/ms-playwright/chromium",
            "Protocol error (Page.navigate): Target closed",
            "net::ERR_NAME_NOT_RESOLVED",
        ],
    )
    def test_anything_else_is_left_alone(self, message: str):
        assert worker._looks_like_no_display(RuntimeError(message)) is False


def test_the_worker_really_does_ask_for_a_display_first():
    """The fallback above is only worth having if headed is what is tried.
    Asserted rather than assumed, because the default has been headed for a
    reason - bot checks - and quietly flipping it would be a real change to
    every crawl."""
    source: Any = open(worker.__file__, encoding="utf-8").read()
    assert "headless = CRAWLER_HEADLESS" in source

class FakeRemoteChromium:
    def __init__(self) -> None:
        self.connected_to: list = []
        self.launches: list = []

    async def connect_over_cdp(self, url: str):
        self.connected_to.append(url)
        return "remote-browser"

    async def launch(self, headless: bool, args=None):
        self.launches.append(headless)
        return "local-browser"


class TestARemoteBrowser:
    """With CRAWLER_BROWSER_WS set, the browser runs on a service such as
    Browserless and this machine starts none, which is what lets a 512 MB
    host crawl at all."""

    def test_it_connects_instead_of_launching(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "wss://example.test?token=secret")
        chromium = FakeRemoteChromium()

        browser = asyncio.run(worker._launch_chromium(FakePlaywright(chromium), headless=False))  # type: ignore[arg-type]

        assert browser == "remote-browser"
        assert chromium.connected_to == ["wss://example.test?token=secret"]
        assert chromium.launches == []

    def test_the_token_is_never_logged(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "wss://example.test?token=secret")

        asyncio.run(worker._launch_chromium(FakePlaywright(FakeRemoteChromium()), headless=False))  # type: ignore[arg-type]

        output = capsys.readouterr()
        assert "secret" not in output.out + output.err


class FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected

    def is_connected(self) -> bool:
        return self.connected


class TestRenewingTheSession:
    """Browserless's free plan ends a session after a minute, so a remote
    session is reopened between pages before that can cut one off."""

    def _session(self, browser: FakeBrowser, age: float) -> Any:
        session = worker.BrowserSession(None, headless=True, on_response=None)
        session.browser = browser
        session._opened_at = worker.time.monotonic() - age
        return session

    def test_a_young_remote_session_is_kept(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "wss://example.test")
        assert self._session(FakeBrowser(), age=5).needs_renewal() is False

    def test_an_old_remote_session_is_renewed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "wss://example.test")
        session = self._session(FakeBrowser(), age=worker.REMOTE_SESSION_SECONDS + 1)
        assert session.needs_renewal() is True

    def test_a_local_browser_lasts_the_whole_crawl(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "")
        assert self._session(FakeBrowser(), age=3600).needs_renewal() is False

    def test_a_disconnected_browser_is_reopened(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "")
        assert self._session(FakeBrowser(connected=False), age=1).needs_renewal() is True


class FakePage:
    def __init__(self) -> None:
        self.viewport: Any = None

    async def add_init_script(self, script: str) -> None:
        pass

    def set_default_timeout(self, ms: int) -> None:
        pass

    async def set_viewport_size(self, size: Any) -> None:
        self.viewport = size

    def on(self, event: str, handler: Any) -> None:
        pass

    def is_closed(self) -> bool:
        return False


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()

    async def route(self, pattern: str, handler: Any) -> None:
        pass

    async def new_page(self) -> FakePage:
        return self.page


class FakeRemoteBrowser:
    def __init__(self) -> None:
        self.contexts = [FakeContext()]
        self.new_contexts = 0

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.new_contexts += 1
        return FakeContext()

    def is_connected(self) -> bool:
        return True


class TestTheRemoteBrowsersOwnContext:
    """The service's documentation says to use the context it already has. A
    context made with new_context() was closed under the crawl, which left one
    empty page and a blank clone."""

    def test_it_uses_the_existing_context(self, monkeypatch: pytest.MonkeyPatch):
        remote = FakeRemoteBrowser()

        async def connect(p: Any, headless: bool) -> Any:
            return remote

        monkeypatch.setattr(worker, "CRAWLER_BROWSER_WS", "wss://example.test")
        monkeypatch.setattr(worker, "_launch_chromium", connect)
        session = worker.BrowserSession(None, headless=True, on_response=lambda r: None)

        asyncio.run(session.open())

        assert remote.new_contexts == 0
        assert session.context is remote.contexts[0]
        assert session.page.viewport == {"width": 1366, "height": 768}

    def test_a_closed_page_is_noticed(self):
        session = worker.BrowserSession(None, headless=True, on_response=None)
        session.browser = FakeBrowser(connected=False)
        session.page = FakePage()
        assert session.page_is_gone() is True
