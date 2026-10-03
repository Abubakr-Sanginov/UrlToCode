from custom_types import InputMode
from prompts.create import build_create_prompt_from_input
from prompts.plan import derive_prompt_construction_plan
from prompts.prompt_types import PromptHistoryMessage, Stack, UserTurnInput
from prompts.message_builder import Prompt
from prompts.update import (
    build_update_prompt_from_file_snapshot,
    build_update_prompt_from_history,
)
from prompts.update.attached_page import (
    build_attached_pages_block,
    fetch_attached_pages,
)


async def _attached_pages_block(prompt: UserTurnInput) -> str | None:
    """Capture pages for URLs the user pasted into an update request.

    The display text is scanned, not full_text: the selected-element markup
    the frontend appends to full_text contains hrefs that are not requests.
    A failed fetch degrades to a normal edit — it must never kill the turn.
    """
    text = (prompt.get("text") or "").strip()
    if not text:
        return None
    try:
        pages = await fetch_attached_pages(text)
    except Exception as e:
        print(f"[Prompt] Attached-page capture failed: {e}")
        return None
    block = build_attached_pages_block(pages)
    return block or None


async def build_prompt_messages(
    stack: Stack,
    input_mode: InputMode,
    generation_type: str,
    prompt: UserTurnInput,
    history: list[PromptHistoryMessage],
    file_state: dict[str, str] | None = None,
    image_generation_enabled: bool = True,
    design_system: str | None = None,
) -> Prompt:
    plan = derive_prompt_construction_plan(
        stack=stack,
        input_mode=input_mode,
        generation_type=generation_type,
        history=history,
        file_state=file_state,
    )

    strategy = plan["construction_strategy"]
    if strategy == "update_from_history":
        return build_update_prompt_from_history(
            stack=stack,
            history=history,
            image_generation_enabled=image_generation_enabled,
            design_system=design_system,
            attached_pages_block=await _attached_pages_block(prompt),
        )
    if strategy == "update_from_file_snapshot":
        assert file_state is not None
        return build_update_prompt_from_file_snapshot(
            stack=stack,
            prompt=prompt,
            file_state=file_state,
            image_generation_enabled=image_generation_enabled,
            design_system=design_system,
            attached_pages_block=await _attached_pages_block(prompt),
        )
    return build_create_prompt_from_input(
        input_mode,
        stack,
        prompt,
        image_generation_enabled,
        design_system,
    )
