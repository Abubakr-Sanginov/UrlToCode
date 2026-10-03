"""Looking at a site the way the people using it do.

One screenshot at one width says the desktop layout and nothing else. A site
that is mostly used on a phone looks completely different on a 375px screen:
a menu collapses into a button, a three-column grid becomes one column, and the
type is twice the size relative to the page. A clone made from the desktop shot
alone is wrong for most of its visitors, and nothing in the capture says so.

So each page is captured at every width worth caring about, and the clone is
generated from all of them and checked against all of them. The widths are the
three that decide the breakpoints almost every stylesheet uses.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Set, Tuple

# name, width, height - the three widths a responsive stylesheet branches on.
VIEWPORTS: Tuple[Tuple[str, int, int], ...] = (
    ("mobile", 375, 812),
    ("tablet", 768, 1024),
    ("desktop", 1440, 900),
)

# The width the original crawl has always used, kept as the clone's canonical
# size so existing runs and the visual check are unaffected.
CANONICAL_WIDTH = 1366

VIEWPORT_BY_NAME: Dict[str, Tuple[int, int]] = {name: (w, h) for name, w, h in VIEWPORTS}


class Viewport(NamedTuple):
    name: str
    width: int
    height: int

    def as_json(self) -> Dict[str, object]:
        return {"name": self.name, "width": self.width, "height": self.height}


def parse_viewports(raw: Optional[str]) -> List[Viewport]:
    """The viewports to capture, from a comma-separated list of names.

    An empty or unusable request falls back to the desktop width alone, which
    is what the crawler did before and keeps a bad request from failing a
    crawl outright.
    """
    if not raw or not raw.strip():
        return [Viewport("desktop", CANONICAL_WIDTH, 768)]

    wanted: List[Viewport] = []
    seen: Set[str] = set()
    for name in raw.split(","):
        key = name.strip().lower()
        if key in VIEWPORT_BY_NAME and key not in seen:
            seen.add(key)
            width, height = VIEWPORT_BY_NAME[key]
            wanted.append(Viewport(key, width, height))
    return wanted or [Viewport("desktop", CANONICAL_WIDTH, 768)]


def responsive(enabled: bool) -> List[Viewport]:
    """Every width worth capturing, or the desktop one when responsive is off."""
    if not enabled:
        return [Viewport("desktop", CANONICAL_WIDTH, 768)]
    # Widest first: the first capture becomes the page's own screenshot and
    # the canonical width everything downstream is built around.
    return [
        Viewport(name, width, height)
        for name, width, height in sorted(VIEWPORTS, key=lambda item: -item[1])
    ]


def screenshot_file_for(base_file: str, viewport_name: str) -> str:
    """The store name of one viewport's screenshot of a page.

    The desktop shot keeps the plain page name so everything already pointing
    at it - the clone run store, the visual check, the preview - keeps
    working; the other widths are suffixed.
    """
    if not base_file:
        return ""
    if viewport_name == "desktop":
        return base_file
    stem, dot, extension = base_file.rpartition(".")
    if not dot:
        return f"{base_file}-{viewport_name}"
    return f"{stem}-{viewport_name}.{extension}"


def layout_notes(screenshots: Dict[str, Dict[str, object]]) -> List[str]:
    """What the extra viewports say about the page, in words.

    The difference between two captures is measured, not guessed at, and the
    result is handed to the model as a fact about the page rather than as two
    more images it has to reason about silently.
    """
    notes: List[str] = []
    widths = {name: meta for name, meta in screenshots.items() if name and meta}

    def height_of(name: str) -> int:
        meta = widths.get(name) or {}
        value = meta.get("height")
        return int(value) if isinstance(value, int) else 0

    if "mobile" in widths and "desktop" in widths:
        mobile_height = height_of("mobile")
        desktop_height = height_of("desktop")
        if mobile_height and desktop_height:
            ratio = mobile_height / desktop_height
            if ratio >= 1.8:
                notes.append(
                    f"On mobile the page is {ratio:.1f}x taller than on desktop: "
                    "columns stack one under another, so the mobile layout is a "
                    "long single column and not a squeezed desktop one."
                )
            elif ratio < 1.1:
                notes.append(
                    "The page is about the same height at both widths, so the "
                    "layout reflows in place rather than stacking."
                )
    if "tablet" in widths and "mobile" in widths:
        tablet_height = height_of("tablet")
        mobile_height = height_of("mobile")
        # A tablet that keeps two columns is SHORTER than the phone, where
        # the same content stacks into one long column. That difference in
        # height is the measurement that says where the breakpoint sits.
        if tablet_height and mobile_height and mobile_height / tablet_height >= 1.5:
            notes.append(
                "Tablet is about half as tall as mobile for the same content: "
                "the tablet keeps two columns where the phone has one, so the "
                "breakpoint sits between the mobile and tablet widths."
            )
    if not notes:
        notes.append(
            "The page is laid out the same at every width, so a single "
            "responsive layout is enough."
        )
    return notes
