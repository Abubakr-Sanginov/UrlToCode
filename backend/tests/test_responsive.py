from typing import Dict

from crawler import responsive as r


# --- which widths -----------------------------------------------------------


def test_the_default_is_the_one_width_the_crawl_always_used():
    # Turning the feature on must be a decision, not a side effect: the
    # canonical width keeps existing runs and the visual check unchanged.
    assert r.responsive(False) == [r.Viewport("desktop", 1366, 768)]


def test_responsive_captures_phone_tablet_and_desktop():
    widths = [viewport.width for viewport in r.responsive(True)]

    assert widths == [1440, 768, 375]


def test_desktop_comes_first_because_it_is_the_canonical_width():
    # The first capture becomes the page's own screenshot, and everything
    # downstream assumes the canonical width.
    assert r.responsive(True)[0].name == "desktop"


# --- store names ------------------------------------------------------------


def test_the_desktop_capture_keeps_the_plain_page_name():
    # Everything already pointing at it - the run store, the visual check, the
    # preview - must keep working without being told about viewports.
    assert r.screenshot_file_for("page-abc123.jpg", "desktop") == "page-abc123.jpg"


def test_other_widths_are_suffixed_so_they_do_not_overwrite_each_other():
    mobile = r.screenshot_file_for("page-abc123.jpg", "mobile")
    tablet = r.screenshot_file_for("page-abc123.jpg", "tablet")

    assert mobile == "page-abc123-mobile.jpg"
    assert tablet == "page-abc123-tablet.jpg"
    assert mobile != tablet


def test_a_store_name_without_an_extension_still_gets_its_suffix():
    assert r.screenshot_file_for("page-abc123", "mobile") == "page-abc123-mobile"


def test_no_capture_yields_no_name():
    assert r.screenshot_file_for("", "mobile") == ""


# --- what the extra captures say -------------------------------------------


def shot(height: int) -> Dict[str, object]:
    return {"file": "x.jpg", "width": 375, "height": height}


def test_a_page_that_stacks_on_a_phone_is_said_to_stack():
    # A phone page two and a half times taller than the desktop one is a
    # single stacked column, not a squeezed desktop layout.
    notes = r.layout_notes(
        {
            "mobile": shot(2500),
            "desktop": {"file": "d.jpg", "width": 1440, "height": 1000},
        }
    )

    assert any("columns stack" in note for note in notes)
    assert any("2.5x" in note for note in notes)


def test_a_page_that_reflows_in_place_is_not_called_stacked():
    notes = r.layout_notes(
        {"mobile": shot(1000), "desktop": {"file": "d.jpg", "width": 1440, "height": 1050}}
    )

    assert any("reflows in place" in note for note in notes)


def test_a_breakpoint_between_phone_and_tablet_is_named():
    notes = r.layout_notes(
        {
            "mobile": shot(2000),
            "tablet": {"file": "t.jpg", "width": 768, "height": 900},
        }
    )

    assert any("breakpoint" in note for note in notes)


def test_a_fixed_layout_is_reported_as_needing_no_media_queries():
    notes = r.layout_notes({"desktop": shot(1000)})

    assert any("same at every width" in note for note in notes)


def test_a_capture_without_a_measurable_height_produces_no_claim():
    # Saying a page stacks when nothing was measured would be a guess.
    notes = r.layout_notes(
        {"mobile": {"file": "m.jpg", "width": 375}, "desktop": {"file": "d.jpg", "width": 1440}}
    )

    assert any("same at every width" in note for note in notes)


# --- explicit selection ----------------------------------------------------


def test_a_named_viewport_can_be_asked_for_on_its_own():
    assert r.parse_viewports("mobile") == [r.Viewport("mobile", 375, 812)]


def test_several_names_can_be_asked_for_at_once():
    names = [viewport.name for viewport in r.parse_viewports("mobile, desktop")]

    assert names == ["mobile", "desktop"]


def test_a_repeated_name_is_only_captured_once():
    assert len(r.parse_viewports("mobile, mobile, MOBILE")) == 1


def test_a_request_for_nothing_recognisable_falls_back_rather_than_failing():
    # Better one desktop capture than a crawl that dies on a typo.
    assert r.parse_viewports("hologram") == [r.Viewport("desktop", 1366, 768)]


def test_an_empty_request_falls_back_to_the_canonical_width():
    assert r.parse_viewports(None) == [r.Viewport("desktop", 1366, 768)]
