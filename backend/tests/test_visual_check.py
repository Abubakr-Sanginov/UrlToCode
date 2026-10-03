import io
from typing import Dict, Tuple

import pytest
from PIL import Image, ImageDraw

import visual_check
from visual_check import FidelityResult, compare_images


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


Box = Tuple[int, int, int, int]


def page(width: int, height: int, blocks: Dict[Box, str]) -> Image.Image:
    """A page of flat colour blocks, standing in for a layout."""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for box, colour in blocks.items():
        draw.rectangle(box, fill=colour)
    return image


LAYOUT: Dict[Box, str] = {
    (0, 0, 1366, 120): "#333333",  # header
    (0, 120, 1366, 700): "#ffffff",  # hero
    (0, 700, 680, 1600): "#eeeeee",  # two columns
    (680, 700, 1366, 1600): "#dddddd",
    (0, 1600, 1366, 1740): "#333333",  # footer
}


def test_identical_pages_score_one():
    image = page(1366, 1740, LAYOUT)
    result = compare_images(png(image), png(image), original_width=1366)

    assert result.ok
    assert result.score == 1.0
    assert result.regions == []
    assert result.height_ratio == 1.0


def test_a_single_changed_area_lowers_the_score_and_is_located():
    original = page(1366, 1740, LAYOUT)
    changed_blocks = dict(LAYOUT)
    changed_blocks[(0, 700, 680, 1600)] = "#ff0000"
    rendered = page(1366, 1740, changed_blocks)

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.ok
    assert 0.7 < result.score < 1.0
    assert len(result.regions) == 1
    region = result.regions[0]
    # The report is in the original's pixel space, not the downscaled one.
    assert region.x < 700
    assert region.width > 300
    assert region.y > 600
    assert region.severity == pytest.approx(1.0, abs=0.1)


def test_a_different_layout_reports_everything_as_wrong():
    original = page(1366, 1740, LAYOUT)
    rendered = page(1366, 1740, {(0, 0, 1366, 1740): "#00ff00"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.score < 0.1
    assert result.regions


def test_a_much_shorter_clone_is_penalised_and_reported():
    original = page(1366, 1740, LAYOUT)
    # The clone only rendered its header: the top matches, the rest is missing.
    rendered = page(1366, 400, {(0, 0, 1366, 120): "#333333"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.height_ratio < 0.3
    # Cropping away the difference would have scored this as a near match.
    assert result.score < 0.5


def test_a_taller_clone_is_also_penalised():
    original = page(1366, 400, {(0, 0, 1366, 120): "#333333"})
    rendered = page(1366, 1740, LAYOUT)

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.height_ratio > 4
    assert result.score < 0.5


def test_a_different_width_is_refused_rather_than_compared():
    original = page(1366, 1740, LAYOUT)
    rendered = page(1280, 1740, LAYOUT)

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert not result.ok
    assert "1366" in result.error and "1280" in result.error


def test_antialiasing_level_differences_are_not_a_mismatch():
    original = page(1366, 800, {(0, 0, 1366, 800): "#ffffff"})
    # One JPEG-generation step's worth of noise across the whole page.
    rendered = page(1366, 800, {(0, 0, 1366, 800): "#f6f6f6"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.score == 1.0
    assert result.regions == []


def test_a_real_difference_is_still_caught_under_the_noise_floor():
    original = page(1366, 800, {(0, 0, 1366, 800): "#ffffff"})
    rendered = page(1366, 800, {(200, 200, 900, 600): "#333333"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.score < 0.9


def test_unreadable_images_report_an_error_instead_of_raising():
    result = compare_images(b"not an image", png(page(10, 10, {})))

    assert not result.ok
    assert result.score == 0.0
    assert "compare" in result.error


def test_regions_are_capped_and_ordered_by_how_wrong_they_are():
    original = page(1366, 1740, LAYOUT)
    changed = dict(LAYOUT)
    # Two separate wrong areas with a correct strip between them, so they are
    # reported as two regions rather than one merged band. The upper one is
    # wrong in about half of its pixels, the lower one is wrong outright.
    for y in range(120, 500, 16):
        for x in range(0, 1366, 16):
            if (x // 16 + y // 16) % 2 == 0:
                changed[(x, y, x + 16, y + 16)] = "#cccccc"
    changed[(0, 800, 1366, 1580)] = "#ff0000"
    rendered = page(1366, 1740, changed)

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert len(result.regions) <= visual_check.MAX_REGIONS
    severities = [region.severity for region in result.regions]
    assert severities == sorted(severities, reverse=True)
    # The repair is pointed at the area that is wrong, not the one that is
    # only partly off.
    assert result.regions[0].severity > 0.9
    assert result.regions[0].y > 700
    assert 0.2 < result.regions[-1].severity < 0.8
    assert result.regions[-1].y < 400


def test_a_thin_page_still_compares():
    # The block size is derived from the smaller side, so a short page must
    # not divide down to nothing or divide by zero.
    original = page(1366, 60, {(0, 0, 1366, 60): "#ffffff"})
    rendered = page(1366, 60, {(0, 0, 1366, 60): "#000000"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert result.ok
    assert result.score < 0.5


def test_result_serialises_for_the_api():
    result = FidelityResult(
        score=0.5,
        compared_width=640,
        compared_height=900,
        height_ratio=1.5,
        regions=[],
    )
    payload: Dict[str, object] = result.to_json()

    assert payload["score"] == 0.5
    assert payload["heightRatio"] == 1.5
    assert payload["regions"] == []
    assert payload["error"] == ""


def test_the_real_picture_sizes_are_reported_alongside_the_score():
    # The comparison runs on a downscaled copy, so the UI cannot work out how
    # big either picture is from the dimensions it is otherwise given.
    original = page(1366, 1740, LAYOUT)
    rendered = page(1366, 900, {(0, 0, 1366, 120): "#333333"})

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert (result.original_width, result.original_height) == (1366, 1740)
    assert (result.rendered_width, result.rendered_height) == (1366, 900)
    assert result.compared_width < result.original_width

    payload = result.to_json()
    assert payload["originalHeight"] == 1740
    assert payload["renderedHeight"] == 900


def test_a_page_that_could_not_be_compared_still_reports_its_sizes():
    original = page(1366, 1740, LAYOUT)
    rendered = page(1280, 1740, LAYOUT)

    result = compare_images(png(original), png(rendered), original_width=1366)

    assert not result.ok
    assert result.original_width == 1366
    assert result.rendered_width == 1280
