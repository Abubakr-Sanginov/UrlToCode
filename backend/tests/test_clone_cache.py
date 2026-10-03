import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

import clone_cache


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cache of its own, so a test never reads the developer's real one."""
    folder = tmp_path / "page-cache"
    monkeypatch.setattr(clone_cache, "CACHE_DIR", folder)
    return folder


def key(**overrides: Any) -> clone_cache.CacheKey:
    values: Dict[str, Any] = {
        "base_url": "https://shop.test",
        "path": "/about",
        "stack": "html_tailwind",
        "model": "gpt-4o",
        "prompt_version": "1",
    }
    values.update(overrides)
    return clone_cache.key_for(**values)


def entry_file(which: clone_cache.CacheKey) -> Path:
    """The file one entry lives in, with the folder it needs already there."""
    which.file().parent.mkdir(parents=True, exist_ok=True)
    return which.file()


# --- what makes a hit -------------------------------------------------------


def test_a_page_that_was_generated_before_comes_back():
    clone_cache.put(key(), "<html>cached</html>")

    assert clone_cache.get(key()) == "<html>cached</html>"


def test_a_page_that_was_never_generated_is_a_miss():
    assert clone_cache.get(key()) is None


# --- what must not be a hit -------------------------------------------------


def test_a_different_page_is_not_the_same_answer():
    clone_cache.put(key(path="/about"), "<html>about</html>")

    assert clone_cache.get(key(path="/contact")) is None


def test_a_different_site_is_not_the_same_answer():
    clone_cache.put(key(base_url="https://shop.test"), "<html>a</html>")

    assert clone_cache.get(key(base_url="https://other.test")) is None


def test_a_different_stack_is_not_the_same_answer():
    # The same page as a Tailwind document and as a React component are two
    # different files, and only one of them is what the user asked for.
    clone_cache.put(key(stack="html_tailwind"), "<html>plain</html>")

    assert clone_cache.get(key(stack="nextjs")) is None


def test_a_different_model_is_not_the_same_answer():
    clone_cache.put(key(model="gpt-4o"), "<html>one</html>")

    assert clone_cache.get(key(model="claude-3-5-sonnet")) is None


def test_a_prompt_that_changed_does_not_reuse_the_old_answer():
    # This is the one that matters. Yesterday's prompt produced something
    # today's prompt would not, and handing it back as a hit is how a tool
    # starts returning stale output and calling it a cache.
    clone_cache.put(key(prompt_version="1"), "<html>old</html>")

    assert clone_cache.get(key(prompt_version="2")) is None


def test_a_database_schema_asked_for_changes_the_answer():
    clone_cache.put(key(generate_database=False), "<html>no schema</html>")

    assert clone_cache.get(key(generate_database=True)) is None


def test_a_page_that_has_gone_stale_is_not_handed_back():
    clone_cache.put(key(), "<html>old</html>")
    # Backdate the entry rather than waiting a week for it.
    path = key().file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["storedAt"] = time.time() - clone_cache.MAX_AGE_SECONDS - 60
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert clone_cache.get(key()) is None


# --- a cache is allowed to fail ---------------------------------------------


def test_a_cache_that_cannot_be_read_is_a_miss_not_a_crash():
    entry_file(key()).write_text("{not json", encoding="utf-8")

    assert clone_cache.get(key()) is None


def test_a_cache_entry_with_no_code_is_a_miss():
    entry_file(key()).write_text(json.dumps({"storedAt": time.time()}), encoding="utf-8")

    assert clone_cache.get(key()) is None


def test_a_cache_entry_with_no_timestamp_is_a_miss():
    entry_file(key()).write_text(json.dumps({"code": "<html>x</html>"}), encoding="utf-8")

    assert clone_cache.get(key()) is None


def test_an_empty_page_is_not_worth_keeping():
    clone_cache.put(key(), "   ")

    assert clone_cache.get(key()) is None


def test_a_cache_that_cannot_be_written_does_not_break_the_run(cache_dir: Path):
    # A read-only disk must not take the generation down with it.
    cache_dir.mkdir(parents=True)
    cache_dir.chmod(0o500)
    try:
        clone_cache.put(key(), "<html>x</html>")
    finally:
        cache_dir.chmod(0o700)


# --- bounded ----------------------------------------------------------------


def test_the_cache_drops_what_is_over_its_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(clone_cache, "MAX_ENTRIES", 5)
    for index in range(12):
        clone_cache.put(key(path=f"/p{index}"), f"<html>{index}</html>")

    assert len(list(clone_cache.CACHE_DIR.glob("*.json"))) <= 5


def test_the_newest_pages_are_the_ones_kept(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(clone_cache, "MAX_ENTRIES", 3)
    for index in range(6):
        clone_cache.put(key(path=f"/p{index}"), f"<html>{index}</html>")
        time.sleep(0.002)

    kept = {index for index in range(6) if clone_cache.get(key(path=f"/p{index}"))}

    # The oldest went; the pages just generated are the ones worth keeping.
    assert kept == {3, 4, 5}


def test_an_unreadable_file_counts_as_the_oldest_thing():
    stale = key(path="/stale")
    entry_file(stale).write_text("garbage", encoding="utf-8")
    for index in range(3):
        clone_cache.put(key(path=f"/p{index}"), f"<html>{index}</html>")

    # A file that cannot be read is as good as expired, and is the first to go.
    assert not stale.file().exists()


# --- the panel --------------------------------------------------------------


def test_the_settings_panel_is_told_what_is_held(cache_dir: Path):
    clone_cache.put(key(), "<html>something</html>")

    info = clone_cache.stats()

    assert info["entries"] == 1
    assert info["bytes"] > 0
    assert info["directory"] == str(cache_dir)


def test_an_empty_cache_reports_nothing_rather_than_failing(cache_dir: Path):
    assert clone_cache.stats()["entries"] == 0


def test_clearing_the_cache_answers_how_much_went():
    for index in range(3):
        clone_cache.put(key(path=f"/p{index}"), f"<html>{index}</html>")

    assert clone_cache.clear() == 3
    assert clone_cache.stats()["entries"] == 0


def test_clearing_an_empty_cache_is_not_an_error():
    assert clone_cache.clear() == 0
