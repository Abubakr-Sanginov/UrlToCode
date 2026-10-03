"""Does the model the user picked actually look at pictures?

This is the question the whole visual half of the tool turns on. A pixel
diff, a fidelity score and a repair prompt are only worth anything if the
model can see the two images being compared. If it cannot, the tool does
not fail loudly - it produces a confident-looking repair based on the
words "the lower third is wrong", which is worse than useless, because
the user cannot tell it apart from a repair that was made by looking.

So the answer is established by asking, not by guessing. The name list
below is a fallback for when there is no key to ask with, not the
authority: "supports vision" is a property of the deployment as much as of
the name, and a model the key cannot reach at all must not be described as
a model that merely cannot see.

The probe asks for a word, not a judgement, because a model that cannot
see tends to guess something plausible and a model that can see gives the
right answer with no effort. The picture is four solid squares in
different colours and the question is which colour is on the right, which
is answerable only by looking.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from llm_http import Image, LlmConfig, ProviderError, complete

# The three answers, because "we have not checked" is not the same as "no"
# and a tool that says no about a model that can see is as wrong as one
# that says yes about a model that cannot.
YES = "yes"
NO = "no"
UNKNOWN = "unknown"

# Model names known to take image input, matched on a prefix so that a
# dated version is covered by its family. Also consulted only without a key.
VISION_PREFIXES: Tuple[str, ...] = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "gpt-6",
    "o1",
    "o3",
    "o4",
    "claude-3",
    "claude-4",
    "claude-5",
    "claude-",
    "gemini-",
    "grok-",
    "llava",
    "pixtral",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "kimi-vl",
    "glm-4v",
    "minimax-vl",
    "internvl",
    "molmo",
)

# Model names known to be text-only. Consulted only when there is no key to
# probe with, so a miss costs nothing but a probe that would have answered
# anyway.
TEXT_ONLY_PREFIXES: Tuple[str, ...] = (
    "deepseek",
    "qwen-",
    "mimo-",
    "hy4",
    "code-",
)

_PROBE_QUESTION = (
    "Look at the image. What colour is the square on the far right? "
    "Answer with one word in English and nothing else. "
    "If you are unsure, give your best guess anyway."
)
# The question asks for a guess on purpose. A model that cannot see often
# refuses instead of answering, and a refusal is ambiguous - it can mean
# "I am blind" or "I am being careful", and those send the user to
# different places. A forced guess is never ambiguous: the right colour
# means it looked, a wrong one means it did not.
_PROBE_ANSWER = "green"

# The answer is one word, but a reasoning model writes its thinking down
# first and counts that against the same budget. At sixteen tokens such a
# model spends everything on reasoning and returns nothing at all, which
# reported a working vision model as unconfirmed - forever, because the
# answer is remembered. A thousand leaves room for a short trace and still
# costs a fraction of a cent.
PROBE_MAX_TOKENS = 1024
# Generous, because this call may include a slow first response from a
# provider that is only just reachable.
PROBE_TIMEOUT_SECONDS = 60

# A 4x1 strip of solid squares. Tiny on purpose: the probe answers a
# question about a 32x32 image, so the cost is a handful of tokens and
# the base64 payload is small enough not to matter on a slow connection.
_PROBE_SIDE = 32
_PROBE_COLOURS = ("red", "blue", "yellow", "green")


def _probe_png() -> bytes:
    """A small picture whose rightmost square is green."""
    from PIL import Image as PILImage

    width = _PROBE_SIDE * len(_PROBE_COLOURS)
    picture = PILImage.new("RGB", (width, _PROBE_SIDE))
    for index, colour in enumerate(_PROBE_COLOURS):
        band = PILImage.new("RGB", (_PROBE_SIDE, _PROBE_SIDE), colour)
        picture.paste(band, (index * _PROBE_SIDE, 0))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def from_name(model: str) -> str:
    """What the model is called, without the provider's decorations.

    Gateways prefix ids ("free/qwen-3.8-max", "anthropic/claude-opus-4.6")
    and the prefix is theirs, not the model's, so it is stripped before
    anything is matched against it.
    """
    name = (model or "").strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def known_capability(model: str) -> str:
    """YES or NO when the name settles it, UNKNOWN when it does not."""
    name = from_name(model)
    if not name:
        return UNKNOWN
    if any(name.startswith(prefix) for prefix in VISION_PREFIXES):
        return YES
    if any(name.startswith(prefix) for prefix in TEXT_ONLY_PREFIXES):
        return NO
    return UNKNOWN


@dataclass
class VisionAnswer:
    """What a model can see, and how we found out."""

    model: str
    capability: str
    source: str  # "name" or "probe"
    detail: str = ""

    @property
    def sees_images(self) -> bool:
        return self.capability == YES

    def to_json(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "capability": self.capability,
            "source": self.source,
            "detail": self.detail,
        }


def _verdict_from_text(text: str) -> Optional[str]:
    """YES, NO, or None when the answer was not one we can read."""
    answer = (text or "").strip().lower()
    if not answer:
        return None
    # A model that says outright that it cannot see is reporting its own
    # blindness. Worth believing: a model that can see the picture has no
    # reason to claim otherwise, and treating the refusal as "we do not
    # know" left a text-only model sitting in the middle forever.
    if _cannot_see(answer):
        return NO
    if _PROBE_ANSWER in answer:
        return YES
    if any(colour in answer for colour in _PROBE_COLOURS):
        return NO
    return None


def _cannot_see(answer: str) -> bool:
    """Does the answer say the model cannot look at pictures?"""
    says_cannot = any(
        phrase in answer
        for phrase in ("cannot", "can't", "can not", "unable", "not able", "don't", "do not")
    )
    talks_images = "image" in answer or "picture" in answer or "see" in answer
    return says_cannot and talks_images


# Probed once per (endpoint, model). A probe is cheap but not free, and the
# answer for a model that has not changed will not have either.
_cache: Dict[Tuple[str, str], VisionAnswer] = {}


def cached(model: str, base_url: str = "") -> Optional[VisionAnswer]:
    return _cache.get((base_url or "", from_name(model)))


async def detect(cfg: LlmConfig, force: bool = False) -> VisionAnswer:
    """Whether this model sees images, asking the model itself.

    The name list is a first guess and a fallback, not the answer. It is
    used when there is no key to ask with, because then there is nothing to
    ask. Everywhere else the probe decides, for two reasons: "supports
    vision" is a property of the deployment as much as of the name, and a
    name-based answer is reported as fact when it is really a guess - which
    is how a model that cannot be called at all gets described as a model
    that simply cannot see.

    One tiny call per model, remembered afterwards, because the answer for
    a model that has not changed will not have either.
    """
    if not cfg.model:
        return VisionAnswer(model="", capability=UNKNOWN, source="name", detail="no model chosen")

    if not cfg.is_usable:
        known = known_capability(cfg.model)
        return VisionAnswer(
            model=cfg.model,
            capability=known,
            source="name",
            detail="no key configured, so this is from the model's name",
        )

    key = (cfg.base_url or "", from_name(cfg.model))
    if not force and key in _cache:
        return _cache[key]

    answer = await _probe(cfg)
    _cache[key] = answer
    return answer


async def _probe(cfg: LlmConfig) -> VisionAnswer:
    model = cfg.model or ""
    try:
        result = await complete(
            cfg,
            system=(
                "You answer questions about images with a single word. "
                "You never explain and never apologise."
            ),
            user=_PROBE_QUESTION,
            max_tokens=PROBE_MAX_TOKENS,
            timeout=PROBE_TIMEOUT_SECONDS,
            images=[Image(data=_probe_png(), mime_type="image/png")],
        )
    except ProviderError as exc:
        # A refusal is not the same as a model that cannot see. A provider
        # that will not serve the model at all - no subscription, no access,
        # retired id - has told us nothing about its eyes, and answering
        # "no" here would send the user off to buy a different model for a
        # problem the model does not have.
        return VisionAnswer(
            model=model,
            capability=UNKNOWN,
            source="probe",
            detail=f"the provider would not serve this model: {exc}"[:200],
        )
    except (asyncio.TimeoutError, OSError) as exc:
        return VisionAnswer(
            model=model, capability=UNKNOWN, source="probe", detail=str(exc)[:200]
        )

    if not result.text:
        return VisionAnswer(
            model=model,
            capability=UNKNOWN,
            source="probe",
            detail=result.empty_reason or "the model said nothing",
        )

    verdict = _verdict_from_text(result.text)
    if verdict is None:
        return VisionAnswer(
            model=model,
            capability=UNKNOWN,
            source="probe",
            detail="the answer did not name a colour",
        )
    return VisionAnswer(model=model, capability=verdict, source="probe")


def list_models_that_see(models: List[str]) -> List[str]:
    """Which of a provider's models are worth offering for a visual job."""
    return [model for model in models if known_capability(model) == YES]


def clear_cache() -> None:
    _cache.clear()


__all__ = [
    "NO",
    "TEXT_ONLY_PREFIXES",
    "UNKNOWN",
    "VISION_PREFIXES",
    "YES",
    "VisionAnswer",
    "cached",
    "clear_cache",
    "detect",
    "from_name",
    "known_capability",
    "list_models_that_see",
]
