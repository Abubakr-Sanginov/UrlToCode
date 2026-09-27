"""screenshot_preview rendering: a pluggable backend behind a stable API."""

from preview_screenshot.base import ScreenshotBackend, VIEWPORT_SIZES


def __getattr__(name: str):
    if name == "PlaywrightBackend":
        from preview_screenshot.playwright_backend import PlaywrightBackend
        return PlaywrightBackend
    if name in ("capture_preview_screenshot", "is_screenshot_preview_available",
                "probe_screenshot_preview", "set_screenshot_backend"):
        from preview_screenshot import registry
        return getattr(registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ScreenshotBackend",
    "VIEWPORT_SIZES",
    "PlaywrightBackend",
    "capture_preview_screenshot",
    "is_screenshot_preview_available",
    "probe_screenshot_preview",
    "set_screenshot_backend",
]
