import json
import subprocess
from typing import Any, Dict, List

import pytest

from crawler import crawler


def _params_of(**kwargs: Any) -> Dict[str, Any]:
    """The worker reads its parameters from stdin, never from argv."""
    return json.loads(kwargs["input"])


def test_timed_out_crawl_keeps_the_pages_it_already_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow SPA used to lose everything when the subprocess overran.

    The worker persists each captured page, so an overrun returns partial
    results instead of the run dying with "Playwright subprocess timed out".
    """

    def fake_run(cmd: List[str], **kwargs: Any) -> None:
        params = _params_of(**kwargs)
        assert not any(arg.startswith("{") for arg in cmd), "params must not reach argv"
        with open(params["output_file"], "w", encoding="utf-8") as fh:
            json.dump({"pages": [{"url": params["url"], "path": "/", "title": "Home"}]}, fh)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

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

    def fake_run(cmd: List[str], **kwargs: Any) -> None:
        params = _params_of(**kwargs)
        with open(params["output_file"], "w", encoding="utf-8") as fh:
            json.dump([{"url": params["url"], "path": "/", "title": "Home"}], fh)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

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

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self) -> None:
            self.stdout = json.dumps(
                {
                    "pages": [{"url": "https://a.com/", "path": "/", "title": "Home"}],
                    "api": {"GET /api/products": {"status": 200, "body": []}},
                }
            )

    monkeypatch.setattr(crawler.subprocess, "run", lambda cmd, **kwargs: Result())

    pages, api, error = crawler._run_playwright_subprocess(
        "https://a.com", max_pages=5, max_depth=2, timeout=15
    )

    assert error is None
    assert len(pages) == 1
    assert "GET /api/products" in api


def test_timed_out_crawl_with_nothing_captured_explains_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: List[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    pages, api, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert pages == []
    assert api == {}
    assert error is not None and "too long to crawl" in error


def test_worker_gets_a_budget_below_the_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker must stop itself before the parent kills it."""
    seen: Dict[str, Any] = {}

    class Result:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(cmd: List[str], **kwargs: Any) -> Result:
        seen["params"] = _params_of(**kwargs)
        seen["timeout"] = kwargs["timeout"]
        return Result()

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    crawler._run_playwright_subprocess(
        "https://youtube.com",
        max_pages=30,
        max_depth=2,
        timeout=15,
        llm_config={"provider": "openrouter", "api_key": "k"},
    )

    assert seen["params"]["budget"] < seen["timeout"]
    assert seen["params"]["budget"] <= crawler.MAX_CRAWL_SECONDS


def test_the_api_key_never_reaches_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command line is readable by every process on the machine."""
    seen: Dict[str, Any] = {}

    class Result:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(cmd: List[str], **kwargs: Any) -> Result:
        seen["cmd"] = cmd
        seen["input"] = kwargs["input"]
        return Result()

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    crawler._run_playwright_subprocess(
        "https://example.com",
        max_pages=1,
        max_depth=1,
        timeout=15,
        llm_config={"provider": "openrouter", "api_key": "sk-secret"},
    )

    assert "sk-secret" not in " ".join(seen["cmd"])
    assert "sk-secret" in seen["input"]
