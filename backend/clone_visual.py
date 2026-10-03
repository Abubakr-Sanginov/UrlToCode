"""Scoring a finished clone against the site it was cloned from.

The crawl captured a full-page screenshot of every original, and the run kept
the generated code for every page, so a clone can be put side by side with its
source without re-crawling anything. This walks the pages, renders each one at
the width the original was captured at, and records how close it came - which
is what "fix this page" then acts on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import clone_runs
from clone_runs import CloneRun
from crawler.media_store import media_path, save_media
from crawler.responsive import CANONICAL_WIDTH
from llm_http import Image
from visual_check import (
    DiffRegion,
    FidelityResult,
    compare_images,
    render_page,
)

# A page this far from its original is not worth repairing automatically: the
# clone is wrong in a way a re-generation will not fix, and each attempt costs
# a model call.
DEFAULT_REPAIR_THRESHOLD = 0.80
# Pages are rendered one at a time below this; above it, a pool is opened.
RENDER_CONCURRENCY = 3


@dataclass
class ViewportCheck:
    """One width: the clone rendered there, against the original there."""

    name: str
    width: int
    result: FidelityResult
    render_file: str = ""
    original_file: str = ""

    def to_json(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "width": self.width,
            "score": self.result.score,
            "heightRatio": self.result.height_ratio,
            "originalWidth": self.result.original_width,
            "originalHeight": self.result.original_height,
            "renderedWidth": self.result.rendered_width,
            "renderedHeight": self.result.rendered_height,
            "regions": [region.to_json() for region in self.result.regions],
            "error": self.result.error,
            "renderFile": self.render_file,
            "originalFile": self.original_file,
        }


@dataclass
class PageCheck:
    path: str
    # Every width the page was checked at. A plain crawl has exactly one.
    viewports: List[ViewportCheck]
    # Where the rendered page was stored, so the UI can show it next to the
    # original instead of asking the user to trust a number.
    render_file: str = ""
    # The original's stored screenshot, which the check compared against.
    original_file: str = ""

    @property
    def primary(self) -> ViewportCheck:
        """The width the diff is shown at: desktop when it was checked."""
        for check in self.viewports:
            if check.name == "desktop":
                return check
        return self.viewports[0]

    @property
    def result(self) -> FidelityResult:
        return self.primary.result

    @property
    def score(self) -> float:
        """The page's score is its worst width.

        A clone that matches on desktop and collapses on a phone is not a
        good clone, and averaging the two would let that pass unnoticed.
        """
        scored = [check.result.score for check in self.viewports if check.result.ok]
        if not scored:
            # Nothing could be compared; report the failure rather than a
            # number that reads like a passing one.
            return min((check.result.score for check in self.viewports), default=0.0)
        return min(scored)

    @property
    def failing_viewports(self) -> List[str]:
        """Widths that fell below the bar, for the repair instruction."""
        return [
            check.name
            for check in self.viewports
            if check.result.ok and check.result.score < DEFAULT_REPAIR_THRESHOLD
        ]

    def to_json(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "score": self.score,
            "heightRatio": self.primary.result.height_ratio,
            "originalWidth": self.primary.result.original_width,
            "originalHeight": self.primary.result.original_height,
            "renderedWidth": self.primary.result.rendered_width,
            "renderedHeight": self.primary.result.rendered_height,
            "regions": [region.to_json() for region in self.primary.result.regions],
            "error": self.primary.result.error,
            "renderFile": self.render_file,
            "originalFile": self.original_file,
            "viewports": [check.to_json() for check in self.viewports],
        }


@dataclass
class RunCheck:
    run_id: str
    pages: List[PageCheck]
    # Pages that could not be checked at all (no original, no renderer).
    skipped: List[str]
    mean_score: float
    threshold: float

    def below_threshold(self) -> List[PageCheck]:
        """Pages wrong enough to be worth another generation."""
        return [
            page
            for page in self.pages
            if page.result.ok and page.result.score < self.threshold
        ]

    def to_json(self) -> Dict[str, object]:
        return {
            "runId": self.run_id,
            "pages": [page.to_json() for page in self.pages],
            "skipped": self.skipped,
            "meanScore": self.mean_score,
            "threshold": self.threshold,
            "needsRepair": [page.path for page in self.below_threshold()],
        }


def original_bytes(screenshot_file: str) -> Optional[bytes]:
    """The stored screenshot of an original page, or None when it is gone."""
    if not screenshot_file:
        return None
    path = media_path(screenshot_file)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def repair_images(page: PageCheck, limit: int = 2) -> List[Image]:
    """The pictures a visual repair is made from, original first.

    A pixel score says how wrong a page is; it does not say what is wrong
    with it. Handed the two screenshots a model that can see can point at
    the heading that is the wrong size or the column that did not come
    across, which is the difference between a fix and a guess.

    Only the widths that actually failed are sent, and the worst one first:
    a page that is right on desktop and wrong on a phone should not spend
    the call on an image that was already correct.
    """
    checks = sorted(
        page.viewports,
        key=lambda check: check.result.score if check.result.ok else -1.0,
    )
    images: List[Image] = []
    for check in checks:
        if len(images) >= limit * 2:
            break
        if check.result.ok and check.result.score >= DEFAULT_REPAIR_THRESHOLD:
            continue
        for key in (check.original_file, check.render_file):
            picture = _stored_picture(key)
            if picture is not None:
                images.append(picture)
    return images


def _stored_picture(store_key: str) -> Optional[Image]:
    """A screenshot as bytes, or None when it is not there any more."""
    if not store_key:
        return None
    name = store_key.split(":")[-1] if store_key.startswith("clone-render:") else store_key
    path = media_path(name)
    if path is None or not path.is_file():
        return None
    try:
        return Image(data=path.read_bytes(), mime_type=_mime_of(path))
    except OSError:
        # A screenshot that cannot be read is not worth failing a repair
        # over; the description alone is still better than nothing.
        return None


def _mime_of(path: Path) -> str:
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return "image/jpeg"
    if path.suffix.lower() == ".webp":
        return "image/webp"
    return "image/png"


def describe_repair_images(images: List[Image]) -> str:
    """A line telling the model which picture is which.

    Without it the model is shown two screenshots of similar-looking pages
    and has to guess which is the target, and a repair that fixes the
    original into the clone is worse than no repair.
    """
    if not images:
        return ""
    if len(images) == 1:
        return (
            "\n\nIMAGE 1 is the ORIGINAL page as it looks on the real site. "
            "Your task is to make the generated page look like this one."
        )
    lines = ["\n\nThe images below are the ORIGINAL and then the CURRENT RENDER:"]
    for index in range(0, len(images), 2):
        lines.append(f"IMAGE {index + 1}: the original page.")
        if index + 1 < len(images):
            lines.append(f"IMAGE {index + 2}: the page as it currently renders.")
    lines.append(
        "Make the second look like the first. They are shown at the same "
        "width, so compare them position by position."
    )
    return "\n".join(lines)


def repair_instruction(page: PageCheck, attempt: int) -> str:
    """What to tell the model about one page that rendered wrong.

    Coordinates alone are close to useless to a model ("fix the area at
    0,1400"), so the regions are described in the page's own terms - how far
    down it they sit and how wrong they are - and the original markup is
    already in the prompt to compare against.
    """
    failing = page.failing_viewports
    width_note = ""
    if failing:
        # Saying which width failed is the single most useful thing to tell a
        # model: a page that is right on desktop and wrong on a phone needs a
        # different fix from one that is wrong everywhere.
        listed = ", ".join(failing)
        if len(failing) == len(page.viewports):
            width_note = (
                f" This is wrong at every width checked ({listed}), so it is "
                "the base layout that is off."
            )
        else:
            width_note = (
                f" It is correct at other widths and wrong at {listed}, so the "
                "problem is in how this layout responds to that width - use "
                "media queries or the stack's responsive primitives, and do "
                "not change the desktop layout."
            )

    if not page.result.ok:
        return "This page did not render the way the original does. Rebuild it to match."

    # The regions are described from the width that did worst, not from the
    # desktop: if the page is right on desktop and wrong on a phone, the
    # desktop regions are the areas that need no attention at all.
    worst = min(page.viewports, key=lambda check: check.result.score)
    regions = worst.result.regions
    if not regions:
        return (
            "This page renders very differently from the original overall, "
            "although no single area stands out. Rebuild the page to match "
            "the original's layout, spacing, colours and typography."
            + width_note
        )

    described = []
    for index, region in enumerate(regions[:4], start=1):
        # The page's height is what makes "the lower third" meaningful here.
        band = _band_name(
            region, worst.result.compared_height, worst.result.height_ratio
        )
        described.append(
            f"{index}. {band} of the page (around y={region.y} in the "
            f"original's pixels, {region.width}x{region.height}px) renders "
            "differently from the original"
        )
    areas = "\n".join(described)

    if attempt == 0:
        return (
            "The page below was compared against a screenshot of the original "
            "and does not match it in these places:\n\n"
            f"{areas}\n\n"
            "Adjust the page so those areas look like the original: match the "
            "layout, spacing, colours, fonts and imagery. Keep everything "
            "else as it is, and do not remove content."
        )
    return (
        "The page still does not match the original in these places:\n\n"
        f"{areas}\n\n"
        "A previous attempt at correcting it did not fix it. Change the "
        "structure of these areas rather than their colours alone - a wrong "
        "section usually has the wrong elements or the wrong order, not just "
        "the wrong styling."
    )


def _band_name(region: DiffRegion, page_height: int, height_ratio: float) -> str:
    """Where on the page a region sits, in words a model can act on."""
    # `compared_height` is the padded height, which is the taller of the two
    # images: the original's when the clone is shorter, and the clone's own
    # when it is taller. Multiplying by the ratio puts it back to the clone's
    # real height, so a region near its bottom is called what it is.
    scale = min(1.0, height_ratio) if height_ratio else 1.0
    clone_height = page_height * scale
    if clone_height <= 0:
        return "an area"
    middle = region.y + region.height / 2
    if middle < clone_height / 3:
        return "the top third"
    if middle < 2 * clone_height / 3:
        return "the middle third"
    return "the bottom third"


def originals_for(run: CloneRun, path: str) -> List[Tuple[str, str, int]]:
    """The stored original screenshots of one page: name, file, width.

    A plain crawl has a single entry at the canonical width. A responsive one
    has one per captured viewport, which is what lets the check score the
    clone on a phone as well as on a desktop.
    """
    if run.crawl is None:
        return []
    for page in run.crawl.pages:
        if page.path != path:
            continue
        shots = page.viewport_screenshots or {}
        if shots:
            found: List[Tuple[str, str, int]] = []
            for name, meta in shots.items():
                if not isinstance(meta, dict):
                    continue
                file_name = meta.get("file")
                if not isinstance(file_name, str) or not file_name:
                    continue
                width = meta.get("width")
                found.append(
                    (str(name), file_name, int(width) if isinstance(width, int) else 0)
                )
            if found:
                return found
        if page.screenshot_file:
            return [("desktop", page.screenshot_file, CANONICAL_WIDTH)]
        return []
    return []


async def check_page(
    run: CloneRun,
    path: str,
    code: str,
    originals: List[Tuple[str, str, int]],
) -> PageCheck:
    """Render one page at every width the original was captured at."""
    from babel_cdn import normalize_babel_cdn

    if not originals:
        return PageCheck(
            path=path,
            viewports=[
                ViewportCheck(
                    name="desktop",
                    width=0,
                    result=FidelityResult(
                        score=0.0,
                        compared_width=0,
                        compared_height=0,
                        error="No original screenshot of this page was kept.",
                    ),
                )
            ],
        )

    document = normalize_babel_cdn(code)
    checks: List[ViewportCheck] = []
    for name, original_file, width in originals:
        original = original_bytes(original_file)
        if original is None:
            checks.append(
                ViewportCheck(
                    name=name,
                    width=width,
                    result=FidelityResult(
                        score=0.0,
                        compared_width=0,
                        compared_height=0,
                        error="The original screenshot is no longer available.",
                    ),
                    original_file=original_file,
                )
            )
            continue

        # The render is kept so the UI can put the two side by side. Losing it
        # only costs the picture, not the score.
        rendered, error = await render_page(document, width=width)
        if error:
            checks.append(
                ViewportCheck(
                    name=name,
                    width=width,
                    result=FidelityResult(
                        score=0.0,
                        compared_width=0,
                        compared_height=0,
                        error=error,
                    ),
                    original_file=original_file,
                )
            )
            continue

        result = compare_images(original, rendered)
        stored = save_media(
            f"clone-render:{run.run_id}:{path}:{name}",
            "image/png",
            rendered,
            overwrite=True,
        )
        checks.append(
            ViewportCheck(
                name=name,
                width=width,
                result=result,
                render_file=stored or "",
                original_file=original_file,
            )
        )

    primary = next((check for check in checks if check.name == "desktop"), checks[0])
    return PageCheck(
        path=path,
        viewports=checks,
        render_file=primary.render_file,
        original_file=primary.original_file,
    )


async def check_run(
    run: CloneRun,
    paths: Optional[List[str]] = None,
    threshold: float = DEFAULT_REPAIR_THRESHOLD,
) -> RunCheck:
    """Score the run's pages against the originals captured while crawling.

    `paths` limits the check to some pages; by default every page the run
    generated is checked.
    """
    from preview_screenshot import registry

    wanted = set(paths) if paths is not None else set(run.pages)

    if not registry.width_rendering_available():
        # Nothing can be compared here, but the run is not broken. Saying so
        # beats scoring every page zero and offering to repair them all.
        return RunCheck(
            run_id=run.run_id,
            pages=[],
            skipped=sorted(wanted),
            mean_score=0.0,
            threshold=threshold,
        )

    to_check = [
        (path, run.pages[path].code)
        for path in sorted(wanted)
        if run.pages[path].code
    ]
    skipped = sorted(wanted - {path for path, _ in to_check})

    slots = asyncio.Semaphore(RENDER_CONCURRENCY)

    async def check_one(path: str, code: str) -> PageCheck:
        async with slots:
            return await check_page(run, path, code, originals_for(run, path))

    results = await asyncio.gather(
        *(check_one(path, code) for path, code in to_check)
    )

    # The score is recorded on the run, so a page's fidelity survives a reload
    # and the version list can show it without re-rendering anything.
    for page in results:
        if any(check.result.ok for check in page.viewports):
            record = run.page(page.path)
            record.fidelity = page.score
    try:
        clone_runs.save_run(run, prune=False)
    except OSError as exc:
        print(f"[VISUAL] Could not record fidelity for {run.run_id}: {exc}")

    scored = [page.score for page in results if page.viewports and page.viewports[0].result.ok]
    return RunCheck(
        run_id=run.run_id,
        pages=list(results),
        skipped=skipped,
        mean_score=sum(scored) / len(scored) if scored else 0.0,
        threshold=threshold,
    )
