import asyncio
from typing import Any

from preview_screenshot.base import VIEWPORT_SIZES

PAGE_LOAD_TIMEOUT_MS = 15000
RENDER_SETTLE_MS = 250


class PlaywrightBackend:
    """Default backend: renders in local headless Chromium.

    Runs locally, so the page can load assets served from localhost
    (e.g. /local-assets/ URLs) that an external screenshot API cannot reach.
    Holds one shared browser, launched lazily and reused across captures.
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def _get_browser(self) -> Any:
        from playwright.async_api import async_playwright

        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"],
                )
            return self._browser

    async def available(self) -> bool:
        try:
            await self._get_browser()
            print("[screenshot_preview] Chromium available — tool enabled.")
            return True
        except Exception as exc:
            print(
                "[screenshot_preview] Chromium unavailable — tool disabled. "
                f"Install it with `playwright install chromium`. Cause: {exc}"
            )
            return False

    async def capture(
        self,
        html: str,
        device: str = "desktop",
        full_page: bool = True,
    ) -> bytes:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        width, height = VIEWPORT_SIZES.get(device, VIEWPORT_SIZES["desktop"])
        return await self.capture_at_width(html, width, height, full_page)

    async def capture_at_width(
        self,
        html: str,
        width: int,
        height: int = 768,
        full_page: bool = True,
    ) -> bytes:
        """Render at an exact viewport, for comparing against a fixed capture.

        The named devices exist for a rough preview. A pixel comparison is only
        meaningful at the width the original was captured at, so the visual
        check passes its own size rather than asking for the nearest preset.
        """
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        browser = await self._get_browser()
        page = await browser.new_page(
            viewport={"width": max(1, width), "height": max(1, height)},
            device_scale_factor=1,
        )
        try:
            try:
                await page.set_content(
                    html,
                    wait_until="networkidle",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                pass
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                pass
            await page.wait_for_timeout(RENDER_SETTLE_MS)
            return await page.screenshot(full_page=full_page, type="png")
        finally:
            await page.close()
