"""Does the clone look like the site it was cloned from?

A clone is judged by how it renders, not by how plausible its markup reads. The
crawl already captured a full-page screenshot of every original page, so the
clone is rendered at the same width and compared against that picture: the
fraction of pixels that disagree is the page's fidelity, and the areas that
disagree are what a repair is pointed at.

Scoring, not vetoing. A pixel diff of two independently rendered pages always
shows *some* difference — antialiasing, a font that loaded on one side and not
the other, an image that had not finished loading. The score says how wrong a
page looks, and the threshold that decides "worth repairing" is the caller's,
so a strict comparison never silently discards a usable page.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from PIL import Image, ImageChops

# The width the crawler captures originals at. A clone rendered at any other
# width would be compared against a picture it was never going to match.
ORIGINAL_WIDTH = 1366
ORIGINAL_VIEWPORT_HEIGHT = 768

# Comparisons run on downscaled copies: a 1366px-wide, 20000px-tall pair is
# ~80M pixels to read, and downscaling costs nothing in accuracy here because
# the differences worth reporting are layout-sized, not single pixels.
COMPARISON_WIDTH = 640
# A full-height page shot is capped so a runaway page cannot exhaust memory.
MAX_RENDER_HEIGHT = 20000

# Per-pixel tolerance. Anti-aliasing and JPEG noise on the original are not
# mistakes in the clone, so a small per-channel difference is not a diff.
CHANNEL_TOLERANCE = 24
# A block counts as changed once this share of its pixels differ.
BLOCK_DIFF_RATIO = 0.12
# Blocks are this many comparison pixels square.
BLOCK_SIZE = 16
# Neighbouring changed blocks within this many blocks are one region, so a
# whole mis-rendered section is reported once instead of as a pile of squares.
REGION_GAP = 2
# Regions smaller than this share of the page are noise, not a section.
MIN_REGION_AREA_RATIO = 0.004
MAX_REGIONS = 12


@dataclass
class DiffRegion:
    """An area of the page that renders differently from the original."""

    x: int
    y: int
    width: int
    height: int
    # Share of this region that differs, 0..1. How wrong it is, not just where.
    severity: float

    def to_json(self) -> Dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "severity": self.severity,
        }


def _no_regions() -> List[DiffRegion]:
    return []


@dataclass
class FidelityResult:
    """How close one rendered page is to its original."""

    # 1.0 is a pixel-perfect match, 0.0 is nothing alike.
    score: float
    compared_width: int
    compared_height: int
    # Full-size pixel dimensions of each picture, before the comparison
    # downscale. The UI positions its diff and outlines from these, so it
    # has to know how big each picture really is, not how big the comparison
    # copy was.
    original_width: int = 0
    original_height: int = 0
    rendered_width: int = 0
    rendered_height: int = 0
    regions: List[DiffRegion] = field(default_factory=_no_regions)
    # Set when the comparison could not be made at all, e.g. the original
    # screenshot is missing or unreadable.
    error: str = ""
    # How much taller or shorter the clone is than the original. A page that
    # renders at half the original's height is wrong even if the part it has
    # matches, so this is reported rather than folded into the score silently.
    height_ratio: float = 1.0

    @property
    def ok(self) -> bool:
        return not self.error

    def to_json(self) -> Dict[str, object]:
        return {
            "score": self.score,
            "comparedWidth": self.compared_width,
            "comparedHeight": self.compared_height,
            "originalWidth": self.original_width,
            "originalHeight": self.original_height,
            "renderedWidth": self.rendered_width,
            "renderedHeight": self.rendered_height,
            "heightRatio": self.height_ratio,
            "regions": [region.to_json() for region in self.regions],
            "error": self.error,
        }


def _load(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _scaled_width(image: Image.Image, width: int) -> Image.Image:
    """Downscale for comparison, never upscale - enlarging invents detail."""
    if image.width == width:
        return image
    if image.width < width:
        return image
    ratio = width / float(image.width)
    return image.resize((width, max(1, round(image.height * ratio))), Image.BILINEAR)


def _fit_heights(
    original: Image.Image, rendered: Image.Image
) -> Tuple[Image.Image, Image.Image, float]:
    """Bring both to the taller of the two, padding the shorter one.

    The originals are full-page shots, so a clone of the right length and a
    clone that is missing a section differ mostly in *height*. Padding the
    shorter image to match keeps that difference visible in the comparison
    instead of cropping it away and scoring the two as equal.
    """
    height = max(original.height, rendered.height)
    height_ratio = rendered.height / float(original.height) if original.height else 1.0
    if original.height != height:
        canvas = Image.new("RGB", (original.width, height))
        canvas.paste(original, (0, 0))
        original = canvas
    if rendered.height != height:
        canvas = Image.new("RGB", (rendered.width, height))
        canvas.paste(rendered, (0, 0))
        rendered = canvas
    return original, rendered, height_ratio


def _diff_mask(original: Image.Image, rendered: Image.Image) -> Image.Image:
    """One pixel per position: black where the two agree, white where not."""
    difference = ImageChops.difference(original, rendered)
    # Any channel past the tolerance marks the pixel as different. The band
    # between the two is flattened, so a font rendered one shade off does not
    # count as a difference.
    per_channel = difference.split()
    mask = per_channel[0]
    for channel in per_channel[1:]:
        mask = ImageChops.lighter(mask, channel)
    return mask.point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0, mode="L")


def _changed_fraction(mask: Image.Image) -> float:
    histogram = mask.histogram()
    total = mask.width * mask.height
    if total == 0:
        return 0.0
    return histogram[255] / float(total)


def _block_severity(mask: Image.Image, block: int) -> List[List[Tuple[int, float]]]:
    """Per-block share of changed pixels, as a grid of (changed, area) cells."""
    width = mask.width // block
    height = mask.height // block
    if width == 0 or height == 0:
        return []

    # Cropping to whole blocks keeps every cell the same size, so a partial
    # strip at the bottom cannot be scored as if it were the whole page.
    grid = mask.crop((0, 0, width * block, height * block))
    pixels = grid.load()
    rows: List[List[Tuple[int, float]]] = []
    for row in range(height):
        cells: List[Tuple[int, float]] = []
        for col in range(width):
            changed = 0
            for y in range(row * block, (row + 1) * block):
                for x in range(col * block, (col + 1) * block):
                    if pixels[x, y]:
                        changed += 1
            cells.append((changed, float(block * block)))
        rows.append(cells)
    return rows


def _merge_regions(
    changed: List[List[bool]], severity: List[List[float]], block: int, scale: float
) -> List[DiffRegion]:
    """Group changed blocks that belong to the same mis-rendered section."""
    height = len(changed)
    width = len(changed[0]) if height else 0
    if width == 0 or height == 0:
        return []

    seen = [[False] * width for _ in range(height)]
    regions: List[DiffRegion] = []
    total_blocks = float(width * height)

    for row in range(height):
        for col in range(width):
            if not changed[row][col] or seen[row][col]:
                continue
            # Flood fill, expanded to neighbours within REGION_GAP so a band of
            # unchanged blocks inside a wrong section does not split it.
            stack = [(row, col)]
            min_r = max_c = row
            max_r = min_c = col
            blocks = 0
            weighted = 0.0
            while stack:
                cur_r, cur_c = stack.pop()
                if cur_r < 0 or cur_c < 0 or cur_r >= height or cur_c >= width:
                    continue
                if seen[cur_r][cur_c] or not changed[cur_r][cur_c]:
                    continue
                seen[cur_r][cur_c] = True
                blocks += 1
                weighted += severity[cur_r][cur_c]
                min_r = min(min_r, cur_r)
                max_r = max(max_r, cur_r)
                min_c = min(min_c, cur_c)
                max_c = max(max_c, cur_c)
                for dr in range(-REGION_GAP, REGION_GAP + 1):
                    for dc in range(-REGION_GAP, REGION_GAP + 1):
                        if dr == 0 and dc == 0:
                            continue
                        stack.append((cur_r + dr, cur_c + dc))

            area = (max_r - min_r + 1) * (max_c - min_c + 1)
            if area / total_blocks < MIN_REGION_AREA_RATIO:
                continue
            regions.append(
                DiffRegion(
                    x=round(min_c * block * scale),
                    y=round(min_r * block * scale),
                    width=round((max_c - min_c + 1) * block * scale),
                    height=round((max_r - min_r + 1) * block * scale),
                    severity=round(weighted / blocks, 4) if blocks else 0.0,
                )
            )

    # The worst regions first: that is what a repair should be told about.
    regions.sort(key=lambda region: region.severity, reverse=True)
    return regions[:MAX_REGIONS]


def compare_images(
    original_png: bytes,
    rendered_png: bytes,
    original_width: int = ORIGINAL_WIDTH,
) -> FidelityResult:
    """Score a rendered page against the original, and say where it differs."""
    try:
        original = _load(original_png)
        rendered = _load(rendered_png)
    except Exception as exc:
        return FidelityResult(
            score=0.0,
            compared_width=0,
            compared_height=0,
            error=f"Could not read an image to compare: {exc}",
        )

    original_size = (original.width, original.height)
    rendered_size = (rendered.width, rendered.height)

    if original.width != rendered.width:
        # Different widths mean different layouts; comparing them pixel for
        # pixel would report a whole-page difference with nothing to act on.
        return FidelityResult(
            score=0.0,
            compared_width=rendered.width,
            compared_height=rendered.height,
            original_width=original_size[0],
            original_height=original_size[1],
            rendered_width=rendered_size[0],
            rendered_height=rendered_size[1],
            height_ratio=rendered.height / float(max(1, original.height)),
            error=(
                f"Rendered at {rendered.width}px but the original was captured "
                f"at {original.width}px."
            ),
        )

    height_ratio = rendered.height / float(original.height) if original.height else 1.0
    original, rendered, _ = _fit_heights(original, rendered)
    original = _scaled_width(original, COMPARISON_WIDTH)
    rendered = _scaled_width(rendered, COMPARISON_WIDTH)
    scale = original.width and (original_width / float(original.width)) or 1.0

    mask = _diff_mask(original, rendered)
    score = 1.0 - _changed_fraction(mask)

    block = max(1, min(BLOCK_SIZE, min(mask.width, mask.height) // 4 or 1))
    grid = _block_severity(mask, block)
    changed = [
        [(changed_pixels / area) >= BLOCK_DIFF_RATIO for changed_pixels, area in row]
        for row in grid
    ]
    severity = [
        [(changed_pixels / area if area else 0.0) for changed_pixels, area in row]
        for row in grid
    ]
    regions = _merge_regions(changed, severity, block, scale)

    return FidelityResult(
        score=round(max(0.0, min(1.0, score)), 4),
        compared_width=original.width,
        compared_height=original.height,
        original_width=original_size[0],
        original_height=original_size[1],
        rendered_width=rendered_size[0],
        rendered_height=rendered_size[1],
        regions=regions,
        height_ratio=round(height_ratio, 4),
    )


async def render_page(
    html: str,
    width: int = ORIGINAL_WIDTH,
    full_page: bool = True,
) -> Tuple[bytes, str]:
    """Render a page to PNG at an exact width; returns `(png, error)`."""
    from preview_screenshot import registry

    try:
        rendered = await registry.get_width_renderer().capture_at_width(
            html, width, ORIGINAL_VIEWPORT_HEIGHT, full_page
        )
    except Exception as exc:
        return b"", f"Could not render the page: {exc}"
    return rendered, ""
