import json
import time
from pathlib import Path

import clone_runs
from clone_runs import (
    ClonePage,
    CloneRun,
    crawl_result_from_json,
    crawl_result_to_json,
    load_run,
    new_run_id,
    run_dir,
    save_run,
)
from crawler.crawler import CrawlPage, CrawlResult


def _store(tmp_path: Path) -> Path:
    """The run store's root inside the test's temporary directory."""
    return tmp_path / "runs"


def _crawl() -> CrawlResult:
    return CrawlResult(
        base_url="https://example.com",
        pages=[
            CrawlPage(
                url="https://example.com/",
                path="/",
                title="Home",
                html="<html><body>hi</body></html>",
                design={"bodyFont": "Inter", "palette": ["#fff"]},
                meta={"lang": "en"},
                navigation=[{"text": "Home", "href": "/"}],
                media={"https://cdn/x.png": "aabbcc.png"},
                screenshot_file="deadbeef.jpg",
                depth=0,
            ),
            CrawlPage(url="https://example.com/about", path="/about", depth=1),
        ],
        design_tokens={"colors": ["#000"]},
    )


def _run(run_id: str = "r1") -> CloneRun:
    now = time.time()
    return CloneRun(
        run_id=run_id,
        base_url="https://example.com",
        stack="nextjs_tailwind",
        params={"maxPages": 10},
        llm={"provider": "openai", "api_key": "sk-secret", "model": "gpt-5"},
        crawl=_crawl(),
        pages={
            "/": ClonePage(path="/", status="complete", code="<html/>", fidelity=0.9),
            "/about": ClonePage(path="/about", status="failed", error="rate limited"),
        },
        portal="<html>portal</html>",
        phase="generating",
        created_at=now,
        updated_at=now,
    )


def test_roundtrip_preserves_crawl_and_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    run = _run()
    save_run(run)

    loaded = load_run("r1")
    assert loaded is not None
    assert loaded.base_url == "https://example.com"
    assert loaded.stack == "nextjs_tailwind"
    assert loaded.params == {"maxPages": 10}
    assert loaded.phase == "generating"

    assert loaded.crawl is not None
    assert [page.path for page in loaded.crawl.pages] == ["/", "/about"]
    home = loaded.crawl.pages[0]
    assert home.title == "Home"
    assert home.design == {"bodyFont": "Inter", "palette": ["#fff"]}
    assert home.media == {"https://cdn/x.png": "aabbcc.png"}
    assert home.screenshot_file == "deadbeef.jpg"
    assert loaded.crawl.pages[1].depth == 1
    assert loaded.crawl.design_tokens == {"colors": ["#000"]}

    assert loaded.pages["/"].code == "<html/>"
    assert loaded.pages["/"].fidelity == 0.9
    assert loaded.pages["/about"].status == "failed"
    assert loaded.pages["/about"].error == "rate limited"
    assert loaded.portal == "<html>portal</html>"


def test_api_key_is_never_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    save_run(_run())

    raw: str = (_store(tmp_path) / "r1" / "run.json").read_text(encoding="utf-8")
    assert "sk-secret" not in raw
    assert "openai" in raw
    # Reading the file back has to work even though the key is gone.
    restored = load_run("r1")
    assert restored is not None
    assert restored.llm == {"provider": "openai", "model": "gpt-5"}


def test_crawl_result_json_roundtrip_survives_missing_fields():
    restored = crawl_result_from_json(crawl_result_to_json(CrawlResult(base_url="x")))
    assert restored is not None
    assert restored.pages == []
    assert restored.error is None
    assert crawl_result_from_json(None) is None


def test_code_map_collects_everything_the_frontend_needs():
    code = _run().code_map()
    assert set(code) == {"project-structure", "/", "database-schema"} or set(code) == {
        "project-structure",
        "/",
    }
    # A failed page contributes no file.
    assert "/about" not in code


def test_load_run_is_none_for_unknown_or_broken_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    assert load_run("missing") is None

    directory: Path = _store(tmp_path) / "broken"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text("{not json", encoding="utf-8")
    assert load_run("broken") is None

    (directory / "run.json").write_text("[]", encoding="utf-8")
    assert load_run("broken") is None


def test_run_dir_refuses_to_escape_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    for bad in ["../escape", "", "a/../../b", "..\\..\\win"]:
        try:
            run_dir(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected {bad!r} to be refused")


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    save_run(_run())
    save_run(_run())  # second write over the same run
    directory: Path = _store(tmp_path) / "r1"
    assert [p.name for p in directory.iterdir()] == ["run.json"]


def test_prune_drops_expired_and_surplus_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    now = time.time()

    # `save_run` re-stamps updated_at, so ordering is set on disk directly.
    def write_at(run: CloneRun, updated_at: float) -> None:
        payload = dict(run.to_json())
        payload["created_at"] = updated_at
        payload["updated_at"] = updated_at
        target: Path = _store(tmp_path) / run.run_id / "run.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    write_at(_run("old"), now - 10 * 24 * 60 * 60)
    write_at(_run("keep0"), now)
    write_at(_run("keep1"), now - 1)
    write_at(_run("keep2"), now - 2)

    removed = clone_runs.prune_runs(max_runs=2, max_age=7 * 24 * 60 * 60)

    # The expired run and the oldest survivor of the two allowed go.
    assert removed == 2
    assert load_run("old") is None
    assert load_run("keep2") is None
    assert load_run("keep0") is not None
    assert load_run("keep1") is not None


def test_prune_drops_unreadable_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    broken: Path = _store(tmp_path) / "broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{not json", encoding="utf-8")

    assert clone_runs.prune_runs(max_runs=50, max_age=10**9) == 1
    assert not broken.exists()


def test_list_runs_reports_newest_first_with_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    now = time.time()
    first = _run("a")
    first.updated_at = now - 100
    save_run(first)

    second = _run("b")
    second.updated_at = now
    second.pages["/"].fidelity = 0.5
    second.pages["/about"].status = "complete"
    save_run(second)

    summaries = clone_runs.list_runs()
    assert [item["runId"] for item in summaries] == ["b", "a"]
    assert summaries[0]["pagesComplete"] == 2
    assert summaries[0]["pagesFailed"] == 0
    assert summaries[0]["meanFidelity"] == 0.5
    # No summary leaks the provider key.
    assert "sk-secret" not in json.dumps(summaries)


def test_delete_run(tmp_path, monkeypatch):
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", _store(tmp_path))
    save_run(_run())
    assert clone_runs.delete_run("r1") is True
    assert load_run("r1") is None
    assert clone_runs.delete_run("r1") is False
    assert clone_runs.delete_run("../escape") is False


def test_run_ids_are_unique_and_filesystem_safe():
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50
    for run_id in ids:
        assert run_id.isascii()
        assert all(ch.isalnum() or ch in "-_" for ch in run_id)


def test_page_creates_on_demand():
    run = CloneRun(run_id="r", base_url="u", stack="s")
    page = run.page("/new")
    assert page.status == clone_runs.PENDING
    # A second lookup returns the same object, not a fresh pending page.
    assert run.page("/new") is page
