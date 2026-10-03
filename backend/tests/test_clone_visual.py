import io
from typing import Any, Dict, List, Optional, cast

import pytest
from PIL import Image

import clone_runs
import clone_visual
import visual_check
from clone_runs import ClonePage, CloneRun
from clone_visual import PageCheck, RunCheck, check_run, repair_instruction
from crawler.crawler import CrawlPage, CrawlResult


def png(colour: str, width: int = 1366, height: int = 900) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def result(
    score: float,
    regions: Optional[List[visual_check.DiffRegion]] = None,
    height_ratio: float = 1.0,
    error: str = "",
):
    return visual_check.FidelityResult(
        score=score,
        compared_width=visual_check.COMPARISON_WIDTH,
        compared_height=900,
        regions=regions or [],
        height_ratio=height_ratio,
        error=error,
    )


# (viewport name, score, extra result fields) for one width.
ViewportSpec = tuple


def check(path: str, score: float, **kwargs) -> PageCheck:
    return page_check(path, [("desktop", score, kwargs)])


def page_check(path: str, viewports: List[ViewportSpec]) -> PageCheck:
    """A PageCheck from (name, score, result-kwargs) per width."""
    return PageCheck(
        path=path,
        viewports=[
            clone_visual.ViewportCheck(
                name=str(name),
                width=1366,
                result=result(float(score), **dict(kwargs)),
            )
            for name, score, kwargs in cast(List[Any], viewports)
        ],
    )

def run_with(pages: Dict[str, str]) -> CloneRun:
    """A run whose pages all have a stored original screenshot."""
    return CloneRun(
        run_id="r1",
        base_url="https://example.com",
        stack="html_tailwind",
        pages={
            path: ClonePage(path=path, status="complete", code=code)
            for path, code in pages.items()
        },
        crawl=CrawlResult(
            base_url="https://example.com",
            pages=[
            CrawlPage(
                url=f"https://example.com{path}", path=path, screenshot_file=f"{path}.png"
            )
            for path in pages
        ],
        ),
    )


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Keep the real store out of the developer's machine."""
    monkeypatch.setattr(clone_runs, "CLONE_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(clone_runs, "save_media", lambda *a, **k: "render.png", raising=False)


@pytest.fixture(autouse=True)
def renderer(monkeypatch):
    """Replace the browser and the image loader with fixed pictures."""
    monkeypatch.setattr(
        "preview_screenshot.registry.width_rendering_available", lambda: True
    )
    monkeypatch.setattr(clone_visual, "save_media", lambda *a, **k: "render.png")
    monkeypatch.setattr(clone_visual, "original_bytes", lambda name: png("#ffffff"))
    monkeypatch.setattr(visual_check, "compare_images", _compare_real)


async def _fake_render(html, width=1366, full_page=True):
    return png("#ffffff"), ""


async def _compare_real(original, rendered, original_width=1366):
    return visual_check.compare_images(original, rendered, original_width=original_width)


@pytest.fixture
def only_about_differs(monkeypatch):
    """A renderer where exactly one page comes out visibly different."""

    async def render(html, width=1366, full_page=True):
        return png("#000000" if "about" in html else "#ffffff"), ""

    monkeypatch.setattr(clone_visual, "render_page", render)


@pytest.fixture
def matching_render(monkeypatch):
    monkeypatch.setattr(clone_visual, "render_page", _fake_render)


async def test_a_page_identical_to_its_original_scores_one(matching_render):
    run = run_with({"/": "<html></html>"})

    checked = await check_run(run)

    assert len(checked.pages) == 1
    assert checked.pages[0].score == 1.0
    assert checked.mean_score == 1.0
    assert checked.below_threshold() == []


async def test_the_score_is_recorded_on_the_run(only_about_differs):
    run = run_with({"/": "<html>home</html>", "/about": "<html>about</html>"})

    await check_run(run)

    assert run.pages["/"].fidelity == 1.0
    assert run.pages["/about"].fidelity == 0.0


async def test_the_score_survives_a_reload_of_the_run(only_about_differs):
    run = run_with({"/about": "<html>about</html>"})
    await check_run(run)
    clone_runs.save_run(run)

    reloaded = clone_runs.load_run("r1")

    assert reloaded is not None
    assert reloaded.pages["/about"].fidelity == 0.0


async def test_only_the_wrong_pages_are_offered_for_repair(only_about_differs):
    run = run_with({"/": "<html>home</html>", "/about": "<html>about</html>"})

    checked = await check_run(run, threshold=0.9)

    assert [page.path for page in checked.below_threshold()] == ["/about"]


async def test_a_missing_original_is_reported_but_not_called_wrong(monkeypatch, matching_render):
    monkeypatch.setattr(clone_visual, "original_bytes", lambda name: None)
    run = run_with({"/": "<html></html>"})

    checked = await check_run(run)

    assert checked.pages[0].result.ok is False
    assert "no longer available" in checked.pages[0].result.error
    assert checked.below_threshold() == []


async def test_a_run_that_cannot_render_here_is_not_reported_as_wrong(monkeypatch):
    monkeypatch.setattr(
        "preview_screenshot.registry.width_rendering_available", lambda: False
    )
    run = run_with({"/": "<html></html>"})

    checked = await check_run(run)

    assert checked.pages == []
    assert checked.skipped == ["/"]
    assert checked.below_threshold() == []


async def test_a_page_that_failed_to_generate_is_skipped(monkeypatch, matching_render):
    run = run_with({"/": "<html></html>"})
    run.pages["/empty"] = ClonePage(path="/empty", status="failed", code="")

    checked = await check_run(run)

    assert [page.path for page in checked.pages] == ["/"]
    assert "/empty" in checked.skipped


async def test_checking_can_be_limited_to_some_pages(only_about_differs):
    run = run_with({"/": "<html>home</html>", "/about": "<html>about</html>"})

    checked = await check_run(run, paths=["/about"])

    assert [page.path for page in checked.pages] == ["/about"]


def test_the_first_repair_hint_says_where_and_how_wrong_the_page_is():
    regions = [
        visual_check.DiffRegion(x=0, y=50, width=1366, height=300, severity=0.9),
        visual_check.DiffRegion(x=0, y=700, width=1366, height=200, severity=0.7),
    ]

    hint = repair_instruction(check("/", 0.5, regions=regions), attempt=0)

    assert "top third" in hint
    assert "bottom third" in hint
    assert "does not match" in hint
    assert "do not remove content" in hint
    # A model needs coordinates it can reason about, not a raw rectangle.
    assert "y=50" in hint


def test_a_second_attempt_is_told_to_change_structure_not_colours():
    regions = [visual_check.DiffRegion(x=0, y=100, width=1366, height=300, severity=0.9)]

    second = repair_instruction(check("/", 0.4, regions=regions), attempt=1)

    assert "still does not match" in second
    assert "structure" in second
    assert "previous attempt" in second.lower()


def test_a_page_with_no_localised_difference_gets_a_general_instruction():
    hint = repair_instruction(check("/", 0.2, regions=[]), attempt=0)

    assert "no single area stands out" in hint
    assert "Rebuild the page" in hint


def test_a_page_that_failed_to_render_is_told_to_rebuild():
    page = check("/", 0.0, error="no renderer")

    assert "Rebuild it to match" in repair_instruction(page, attempt=0)


def test_a_page_half_the_originals_height_is_described_by_its_own_height():
    # compared_height is padded to the taller image, so the band has to be
    # worked out against the clone's real height, not the padded one.
    regions = [visual_check.DiffRegion(x=0, y=600, width=1366, height=200, severity=0.9)]
    page = check("/", 0.4, regions=regions, height_ratio=0.5)

    # The padded height is 900 and the clone rendered 450, so y=600 is past
    # the clone's own end and the area is described as the bottom of it.
    assert "bottom third" in repair_instruction(page, attempt=0)


def test_run_check_serialises_what_the_ui_needs():
    checked = RunCheck(
        run_id="r1",
        pages=[check("/about", 0.4)],
        skipped=["/contact"],
        mean_score=0.4,
        threshold=0.8,
    )

    payload = checked.to_json()

    assert payload["runId"] == "r1"
    assert payload["needsRepair"] == ["/about"]
    assert payload["skipped"] == ["/contact"]
    assert payload["threshold"] == 0.8


# --- more than one width ----------------------------------------------------


def responsive_run(matching: str = "home") -> CloneRun:
    """A run whose page was crawled at three widths."""
    run = run_with({"/": f"<html>{matching}</html>"})
    crawl = run.crawl
    assert crawl is not None
    crawl.pages[0].viewport_screenshots = {
        "desktop": {"file": "page-desktop.png", "width": 1440, "height": 900},
        "mobile": {"file": "page-mobile.png", "width": 375, "height": 1600},
        "tablet": {"file": "page-tablet.png", "width": 768, "height": 1100},
    }
    return run


async def test_a_page_is_checked_at_every_width_the_original_was_captured_at(
    monkeypatch,
):
    seen: List[int] = []

    async def render(html, width=1366, full_page=True):
        seen.append(width)
        return png("#ffffff"), ""

    monkeypatch.setattr(clone_visual, "render_page", render)

    checked = await check_run(responsive_run())

    assert sorted(seen) == [375, 768, 1440]
    assert len(checked.pages[0].viewports) == 3


async def test_a_page_that_is_right_on_desktop_and_wrong_on_a_phone_scores_low(
    monkeypatch,
):
    # The whole point of checking several widths: averaging or reporting only
    # the desktop would pass a page that collapses on the device most people
    # actually use.
    async def render(html, width=1366, full_page=True):
        return png("#000000" if width < 500 else "#ffffff"), ""

    monkeypatch.setattr(clone_visual, "render_page", render)

    checked = await check_run(responsive_run())

    page = checked.pages[0]
    assert page.score == 0.0
    assert page.failing_viewports == ["mobile"]


def test_a_failing_width_is_named_so_the_fix_is_about_that_width():
    page = page_check(
        "/",
        [
            ("desktop", 0.97, {}),
            ("mobile", 0.31, {}),
            ("tablet", 0.88, {}),
        ],
    )

    hint = repair_instruction(page, attempt=0)

    assert "mobile" in hint
    assert "media queries" in hint
    # The desktop is fine; changing it would make the page worse.
    assert "do not change the desktop layout" in hint


def test_a_page_wrong_at_every_width_is_told_the_base_layout_is_off():
    page = page_check(
        "/",
        [("desktop", 0.2, {}), ("mobile", 0.1, {}), ("tablet", 0.3, {})],
    )

    hint = repair_instruction(page, attempt=0)

    assert "every width" in hint
    assert "base layout" in hint


def test_the_described_regions_come_from_the_worst_width_not_the_desktop():
    # Pointing a repair at the desktop's regions when only the phone is wrong
    # would send the model to fix the part that already works.
    desktop = [visual_check.DiffRegion(x=0, y=0, width=1366, height=100, severity=0.9)]
    mobile = [visual_check.DiffRegion(x=0, y=800, width=375, height=200, severity=0.9)]

    page = page_check(
        "/",
        [("desktop", 0.95, {"regions": desktop}), ("mobile", 0.2, {"regions": mobile})],
    )

    hint = repair_instruction(page, attempt=0)

    assert "y=800" in hint
    assert "y=0 " not in hint


def test_the_desktop_render_is_the_one_the_ui_shows():
    page = page_check(
        "/",
        [("mobile", 0.1, {}), ("desktop", 0.9, {}), ("tablet", 0.8, {})],
    )

    assert page.primary.name == "desktop"


def test_the_worst_width_is_reported_to_the_api():
    payload = page_check(
        "/",
        [("desktop", 0.9, {}), ("mobile", 0.2, {})],
    ).to_json()

    assert payload["score"] == 0.2
    entries = cast(List[Dict[str, object]], payload["viewports"])
    widths = [str(entry["name"]) for entry in entries]
    assert widths == ["desktop", "mobile"]


def test_a_page_with_no_comparable_width_is_not_given_a_passing_score():
    page = page_check("/", [("desktop", 0.0, {"error": "no renderer"})])

    assert page.score == 0.0
    assert page.failing_viewports == []
