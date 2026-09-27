from fastapi import APIRouter
from pydantic import BaseModel

from preview_screenshot.registry import probe_screenshot_preview

router = APIRouter()


class Capabilities(BaseModel):
    screenshot_preview: bool


@router.get("/api/capabilities", response_model=Capabilities)
async def get_capabilities() -> Capabilities:
    return Capabilities(screenshot_preview=await probe_screenshot_preview())
