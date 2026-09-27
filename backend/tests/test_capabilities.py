import pytest
from routes import capabilities


@pytest.mark.asyncio
async def test_capabilities_reports_screenshot_preview_unavailable(monkeypatch) -> None:
    async def fake_probe() -> bool:
        return False

    monkeypatch.setattr(capabilities, "probe_screenshot_preview", fake_probe)
    result = await capabilities.get_capabilities()
    assert result.screenshot_preview is False


@pytest.mark.asyncio
async def test_capabilities_reports_screenshot_preview_available(monkeypatch) -> None:
    async def fake_probe() -> bool:
        return True

    monkeypatch.setattr(capabilities, "probe_screenshot_preview", fake_probe)
    result = await capabilities.get_capabilities()
    assert result.screenshot_preview is True
