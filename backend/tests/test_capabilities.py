import pytest
from routes.capabilities import get_capabilities


@pytest.mark.asyncio
async def test_capabilities_reports_screenshot_preview_unavailable() -> None:
    result = await get_capabilities()
    assert result.screenshot_preview is False
