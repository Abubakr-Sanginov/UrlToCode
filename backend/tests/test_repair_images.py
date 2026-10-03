"""A visual repair is made from looking, or it is a guess.

The repair prompt used to describe where the differences were in words -
"the lower third, around y=1400" - and hand the model no picture at all.
These tests pin down that the two screenshots now reach a model that can
see, and that nothing is sent to one that cannot.
"""

from pathlib import Path
from typing import Any, List, Tuple

import pytest

import clone_visual
from clone_visual import PageCheck, ViewportCheck
from llm_http import Image
from visual_check import DiffRegion, FidelityResult


@pytest.fixture(autouse=True)
def media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Screenshots of this test's own, so nothing reads the real store."""
    store = tmp_path / "media"
    store.mkdir()
    monkeypatch.setattr(clone_visual, "media_path", lambda name: store / name)
    return store


def check(
    name: str = "desktop",
    score: float = 0.5,
    *,
    store_key: str = "shot.png",
    original_key: str | None = None,
    regions: List[DiffRegion] | None = None,
    ok: bool = True,
) -> ViewportCheck:
    return ViewportCheck(
        name=name,
        width=1440,
        result=FidelityResult(
            score=score,
            compared_width=1440,
            compared_height=2400,
            regions=regions or [],
            error="" if ok else "could not render",
        ),
        render_file=store_key,
        original_file=original_key or store_key,
    )


def page(*viewports: ViewportCheck) -> PageCheck:
    return PageCheck(path="/", viewports=list(viewports))


def store(media_dir: Path, name: str, colour: str) -> str:
    from PIL import Image as PILImage

    picture = PILImage.new("RGB", (40, 40), colour)
    picture.save(media_dir / name, format="PNG")
    return name


# --- putting the pictures together -------------------------------------------


def test_a_repair_is_shown_the_original_and_the_render(media_dir: Path) -> None:
    store(media_dir, "orig.png", "red")
    store(media_dir, "render.png", "blue")
    subject = page(
        ViewportCheck(
            name="desktop",
            width=1440,
            result=FidelityResult(
                score=0.5,
                compared_width=1440,
                compared_height=2400,
                regions=[],
                error="",
            ),
            render_file="render.png",
            original_file="orig.png",
        )
    )

    pictures = clone_visual.repair_images(subject)

    assert len(pictures) == 2
    assert all(isinstance(picture, Image) for picture in pictures)
    assert pictures[0].data != pictures[1].data


def test_the_original_comes_first(media_dir: Path) -> None:
    # The instruction says the first one is the original, and the model
    # would otherwise fix the clone into matching the thing it should be
    # copying.
    store(media_dir, "orig.png", "red")
    store(media_dir, "render.png", "blue")
    subject = page(
        ViewportCheck(
            name="desktop",
            width=1440,
            result=FidelityResult(
                score=0.5, compared_width=1440, compared_height=2400, error=""
            ),
            render_file="render.png",
            original_file="orig.png",
        )
    )

    subject_pictures = clone_visual.repair_images(subject)

    assert subject_pictures[0].data == (media_dir / "orig.png").read_bytes()


def test_a_page_that_already_matches_is_not_shown_to_the_model(
    media_dir: Path,
) -> None:
    # Spending a call on an image that was already right is how a run runs
    # out of budget fixing nothing.
    store(media_dir, "ok.png", "green")
    subject = page(check(store_key="ok.png", score=0.99))

    assert clone_visual.repair_images(subject) == []


def test_only_the_failing_width_is_sent(media_dir: Path) -> None:
    # A page that is right on desktop and wrong on a phone should not spend
    # the call on a desktop screenshot that needs nothing.
    store(media_dir, "desktop.png", "green")
    store(media_dir, "mobile.png", "red")
    subject = page(
        check("desktop", 0.99, store_key="desktop.png"),
        check("mobile", 0.4, store_key="mobile.png"),
    )

    pictures = clone_visual.repair_images(subject)

    assert len(pictures) == 2
    assert pictures[0].data == (media_dir / "mobile.png").read_bytes()


def test_a_screenshot_that_is_gone_does_not_fail_the_repair(
    media_dir: Path,
) -> None:
    store(media_dir, "render.png", "blue")

    pictures = clone_visual.repair_images(
        page(check(store_key="render.png", original_key="never-saved.png"))
    )

    # The original is missing, so only the render is available. Losing the
    # comparison is better than losing the repair.
    assert len(pictures) == 1


def test_a_run_key_is_reduced_to_the_file_it_names(media_dir: Path) -> None:
    store(media_dir, "render.png", "blue")

    pictures = clone_visual.repair_images(
        page(
            check(
                store_key="clone-render:r1:/:render.png",
                original_key="clone-render:r1:/:gone.png",
            )
        )
    )

    assert len(pictures) == 1


def test_a_jpeg_is_described_as_a_jpeg(media_dir: Path) -> None:
    from PIL import Image as PILImage

    PILImage.new("RGB", (20, 20), "red").save(media_dir / "shot.jpg", format="JPEG")

    pictures = clone_visual.repair_images(page(check(store_key="shot.jpg")))

    assert pictures
    assert all(p.mime_type == "image/jpeg" for p in pictures)


# --- telling the model which picture is which --------------------------------


def test_one_picture_is_named_as_the_original() -> None:
    line = clone_visual.describe_repair_images([Image(data=b"x")])

    assert "ORIGINAL" in line
    assert "IMAGE 1" in line


def test_two_pictures_are_labelled_in_order() -> None:
    line = clone_visual.describe_repair_images([Image(data=b"x"), Image(data=b"y")])

    assert "IMAGE 1: the original page." in line
    assert "IMAGE 2: the page as it currently renders." in line


def test_no_pictures_means_nothing_is_said_about_pictures() -> None:
    # A blank line would leave the model told about differences it is not
    # being shown.
    assert clone_visual.describe_repair_images([]) == ""


def test_the_description_keeps_the_words_for_a_blind_model(media_dir: Path) -> None:
    # The region description is what a model that cannot see works from, so
    # it has to survive the change and not be replaced by the pictures.
    regions = [DiffRegion(x=0, y=1400, width=100, height=50, severity=0.9)]
    hint = clone_visual.repair_instruction(page(check(regions=regions)), attempt=0)

    assert "y=1400" in hint
