from typing import Optional

from preview_screenshot.base import ScreenshotBackend

_backend: Optional[ScreenshotBackend] = None


def _get_backend() -> ScreenshotBackend:
    global _backend
    if _backend is None:
        from preview_screenshot.playwright_backend import PlaywrightBackend
        _backend = PlaywrightBackend()
    return _backend


_available: Optional[bool] = None


def set_screenshot_backend(backend: ScreenshotBackend) -> None:
    global _backend
    _backend = backend


async def probe_screenshot_preview() -> bool:
    global _available
    if _available is None:
        try:
            backend = _get_backend()
            _available = await backend.available()
        except Exception:
            _available = False
    return _available


def is_screenshot_preview_available() -> bool:
    return _available if _available is not None else False


async def capture_preview_screenshot(
    html: str,
    device: str = "desktop",
    full_page: bool = True,
) -> bytes:
    from babel_cdn import normalize_babel_cdn
    backend = _get_backend()
    return await backend.capture(normalize_babel_cdn(html), device, full_page)
