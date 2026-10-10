import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

import pytest

from crawler import crawler


class _Stream:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def __iter__(self):
        return iter(self._text.splitlines(keepends=True))

    def close(self) -> None:
        pass


def _worker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    hang: bool = False,
    on_stdin: Optional[Callable[[Dict[str, Any]], None]] = None,
    record: Optional[Dict[str, Any]] = None,
) -> None:
    """Put a stand-in worker in place of the real process.

    Popen rather than run, because the parent now polls while the worker runs
    so it can report pages as they are captured. A fake that can only finish
    in one call no longer describes what the parent does, and patching run
    would leave the real worker spawning - which is how this file's tests came
    to crawl youtube.com for real.

    `hang` is a worker that never finishes, which is what running past the
    budget looks like from here.
    """

    class Fake:
        def __init__(self, cmd: List[str], **kwargs: Any) -> None:
            self._cmd = cmd
            if record is not None:
                record["cmd"] = cmd
            self.returncode = returncode
            self.stdout = _Stream(stdout)
            self.stderr = _Stream(stderr)
            self._killed = False
            self.stdin = self

        def write(self, data: str) -> None:
            assert not any(str(arg).startswith("{") for arg in self._cmd), (
                "params must not reach argv"
            )
            if on_stdin is not None:
                on_stdin(json.loads(data))

        def close(self) -> None:
            pass

        def wait(self, timeout: Optional[float] = None) -> int:
            if hang and not self._killed:
                raise subprocess.TimeoutExpired("worker", timeout or 0.0)
            return self.returncode

        def kill(self) -> None:
            self._killed = True

    monkeypatch.setattr(crawler.subprocess, "Popen", Fake)


def _short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an overrun happen now instead of in two minutes."""
    monkeypatch.setattr(crawler, "MAX_CRAWL_SECONDS", 1)
    monkeypatch.setattr(crawler, "GRACE_SECONDS", 0)


def _captured_one_page(params: Dict[str, Any]) -> None:
    with open(params["output_file"], "w", encoding="utf-8") as fh:
        json.dump({"pages": [{"url": params["url"], "path": "/", "title": "Home"}]}, fh)


def test_timed_out_crawl_keeps_the_pages_it_already_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow SPA used to lose everything when the subprocess overran.

    The worker persists each captured page, so an overrun returns partial
    results instead of the run dying with "Playwright subprocess timed out".
    """
    _short_budget(monkeypatch)
    _worker(
        monkeypatch,
        hang=True,
        on_stdin=_captured_one_page,
    )

    pages, api, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert error is None
    assert [p["path"] for p in pages] == ["/"]


def test_pages_saved_by_an_older_worker_are_still_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Salvage must not depend on the worker's output shape.

    A partial file written before the capture was included is a bare list;
    those pages are worth keeping even though no answers came with them.
    """
    _short_budget(monkeypatch)

    def write_bare_list(params: Dict[str, Any]) -> None:
        with open(params["output_file"], "w", encoding="utf-8") as fh:
            json.dump([{"url": params["url"], "path": "/", "title": "Home"}], fh)

    _worker(monkeypatch, hang=True, on_stdin=write_bare_list)

    pages, api, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert error is None
    assert [p["path"] for p in pages] == ["/"]
    assert api == {}


def test_captured_answers_come_back_with_the_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API the site answered with has to reach the caller."""
    _worker(
        monkeypatch,
        stdout=json.dumps(
            {
                "pages": [{"url": "https://a.com/", "path": "/", "title": "Home"}],
                "api": {"GET /api/products": {"status": 200, "body": []}},
            }
        ),
    )

    pages, api, error = crawler._run_playwright_subprocess(
        "https://a.com", max_pages=5, max_depth=2, timeout=15
    )

    assert error is None
    assert len(pages) == 1
    assert "GET /api/products" in api


def test_timed_out_crawl_with_nothing_captured_explains_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_budget(monkeypatch)
    _worker(monkeypatch, hang=True)

    pages, api, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert pages == []
    assert api == {}
    assert error is not None and "too long to crawl" in error


def test_a_crashed_worker_says_what_it_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error nobody can act on is half an answer. The last line of the
    worker's output is what tells a person whether to retry or give up."""
    _worker(
        monkeypatch,
        stderr="Traceback...\nExecutable doesn't exist at /root/.cache",
        returncode=1,
    )

    pages, api, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert pages == []
    assert error is not None and "Executable doesn't exist" in error


def test_worker_gets_a_budget_below_the_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker must stop itself before the parent kills it."""
    seen: Dict[str, Any] = {}

    _worker(monkeypatch, stdout="[]", on_stdin=lambda params: seen.update(params))

    crawler._run_playwright_subprocess(
        "https://youtube.com",
        max_pages=30,
        max_depth=2,
        timeout=15,
        llm_config={"provider": "openrouter", "api_key": "k"},
    )

    budget = seen["budget"]
    assert budget <= crawler.MAX_CRAWL_SECONDS
    assert budget < budget + crawler.GRACE_SECONDS


def test_the_api_key_never_reaches_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command line is readable by every process on the machine."""
    seen: Dict[str, Any] = {}

    _worker(
        monkeypatch,
        stdout="[]",
        on_stdin=lambda params: seen.update(params),
        record=seen,
    )

    crawler._run_playwright_subprocess(
        "https://example.com",
        max_pages=1,
        max_depth=1,
        timeout=15,
        llm_config={"provider": "openrouter", "api_key": "sk-secret"},
    )

    assert "sk-secret" not in " ".join(seen["cmd"])
    assert seen["llm_config"]["api_key"] == "sk-secret"


class TestTellingSomebodyTheCrawlIsMoving:
    def test_pages_are_reported_before_the_crawl_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of polling.

        Without it a 30-page run sits on "0 of 30" for the whole crawl, which
        is indistinguishable from a hang - and people reload a page that looks
        stuck, and pay for it twice.
        """
        _short_budget(monkeypatch)
        seen: List[Any] = []

        def three_pages_then_hang(params: Dict[str, Any]) -> None:
            with open(params["output_file"], "w", encoding="utf-8") as fh:
                json.dump(
                    {"pages": [{"url": f"https://a.com/{i}", "path": f"/{i}"} for i in range(3)]},
                    fh,
                )

        _worker(
            monkeypatch,
            hang=True,
            on_stdin=three_pages_then_hang,
        )

        crawler._run_playwright_subprocess(
            "https://a.com",
            max_pages=30,
            max_depth=2,
            timeout=15,
            report_progress=lambda current, total: seen.append((current, total)),
        )

        assert seen, "nothing was reported while the crawl was running"
        assert seen[-1][0] == 3
        assert seen[-1][1] == 30

    def test_a_crawl_that_finds_nothing_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A count that goes up for pages that do not exist is worse than a
        counter that stays still."""
        _short_budget(monkeypatch)
        seen: List[Any] = []
        _worker(monkeypatch, hang=True)

        crawler._run_playwright_subprocess(
            "https://a.com",
            max_pages=30,
            max_depth=2,
            timeout=15,
            report_progress=lambda current, total: seen.append((current, total)),
        )

        assert seen == []

    def test_no_callback_is_fine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI path has no websocket and asks for no progress."""
        _worker(monkeypatch, stdout="[]")

        pages, api, error = crawler._run_playwright_subprocess(
            "https://a.com", max_pages=5, max_depth=2, timeout=15
        )

        assert error is None
        assert pages == []
