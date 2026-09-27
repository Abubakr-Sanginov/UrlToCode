import json
import subprocess
from typing import Any, Dict, List

import pytest

from crawler import crawler
from crawler import playwright_worker as worker


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
            json.dump([{"url": params["url"], "path": "/", "title": "Home"}], fh)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    pages, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert error is None
    assert [p["path"] for p in pages] == ["/"]


def test_timed_out_crawl_with_nothing_captured_explains_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: List[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    pages, error = crawler._run_playwright_subprocess(
        "https://youtube.com", max_pages=5, max_depth=2, timeout=15
    )

    assert pages == []
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


def test_password_and_signup_fields_are_never_typed_into() -> None:
    assert worker._looks_sensitive({"selector": "#password", "type": "password"})
    assert worker._looks_sensitive({"selector": "#f", "placeholder": "Email address"})
    assert worker._looks_sensitive({"selector": "#f", "name": "newsletter_email"})
    assert not worker._looks_sensitive(
        {"selector": "input[name='q']", "placeholder": "Search", "type": "search"}
    )


def test_fallback_search_prefers_a_real_search_box_and_a_site_query() -> None:
    """With no model suggestions we still exercise the site's own search."""
    action = worker._fallback_search_action(
        [
            {"kind": "text-input", "selector": "#coupon", "placeholder": "Coupon"},
            {"kind": "search-input", "selector": "input[name='q']", "placeholder": "Search"},
        ],
        title="Wikipedia encyclopedia",
        base_url="https://wikipedia.org",
    )

    assert action is not None
    assert action["selector"] == "input[name='q']"
    assert action["value"] == "Wikipedia"


def test_fallback_search_falls_back_to_the_domain_name() -> None:
    action = worker._fallback_search_action(
        [{"kind": "search-input", "selector": "#s", "placeholder": "Search"}],
        title="404",
        base_url="https://www.youtube.com",
    )

    assert action is not None and action["value"] == "youtube"


def test_fallback_search_never_types_on_a_sign_in_page() -> None:
    """YouTube bounces to accounts.google.com; the form there is credentials."""
    assert (
        worker._fallback_search_action(
            [{"kind": "search-input", "selector": "#q", "placeholder": "Search"}],
            title="Sign in - Google Accounts",
            base_url="https://accounts.google.com",
            page_url="https://accounts.google.com/v3/signin/identifier",
        )
        is None
    )


def test_fallback_search_ignores_plain_text_inputs() -> None:
    """Only a field the page calls search is safe to submit unattended."""
    assert (
        worker._fallback_search_action(
            [{"kind": "text-input", "selector": "#identifierId", "placeholder": ""}],
            title="Sign in",
            base_url="https://accounts.google.com",
        )
        is None
    )


def test_fallback_search_skips_pages_with_only_sensitive_fields() -> None:
    assert (
        worker._fallback_search_action(
            [{"kind": "search-input", "selector": "#login", "name": "login"}],
            title="Sign in",
            base_url="https://example.com",
        )
        is None
    )


def test_consent_buttons_are_recognised_and_rejections_are_not() -> None:
    """YouTube answers a crawler with a consent wall in the local language."""
    assert worker.CONSENT_TEXT_RE.match("Принять все")
    assert worker.CONSENT_TEXT_RE.match("Accept all")
    assert worker.CONSENT_TEXT_RE.match("Alle akzeptieren")
    assert not worker.CONSENT_TEXT_RE.match("Отклонить все")
    assert not worker.CONSENT_TEXT_RE.match("Другие варианты")


def test_sign_in_controls_are_skipped_while_clicking() -> None:
    """Clicking "sign in" navigates off-site and poisons the capture."""
    assert worker.SIGN_IN_TEXT_RE.match("Войти в аккаунт")
    assert worker.SIGN_IN_TEXT_RE.match("Sign in")
    assert worker.SIGN_IN_TEXT_RE.match("Log In")
    assert not worker.SIGN_IN_TEXT_RE.match("Показать ещё")
    assert not worker.SIGN_IN_TEXT_RE.match("Subscriptions")


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
