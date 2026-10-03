"""Reading what is inside a frame or a shadow root.

`page.content()` returns the host document. Anything an `<iframe>` rendered, and
anything inside a shadow root, is invisible to it - so a page that is mostly a
map, a video player, a checkout widget or a web component arrives at the model
as an empty `<div>`. The screenshot shows the content; the markup does not, and
the model is asked to copy the markup.

Both are still readable from the browser, so they are read here and attached
to the page as ordinary structures. What comes back is clearly marked as
coming from a frame, so a model is never told that an embedded document is
part of the page it is copying.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

# A frame is only worth reading if it is a real document, not a tracking pixel
# or an ad slot that would be filled with noise.
MIN_FRAME_CHARS = 200
MAX_FRAMES = 8
MAX_FRAME_HTML = 40_000

# How much of a shadow root to read. A component's internals are what make it
# look the way it does, so this is generous, but not unbounded.
MAX_SHADOW_ROOTS = 12
MAX_SHADOW_HTML = 20_000

# Runs in the page. Walks the light DOM, then every open shadow root, then
# every same-origin frame - the ones the browser will let a script read at all.
COLLECT_JS = """
() => {
  const out = { shadow: [], frames: [], sameOrigin: 0, crossOrigin: 0 };
  const seen = new Set();

  function walkShadow(root, host) {
    if (out.shadow.length >= %d || !root) return;
    let html = '';
    try { html = root.innerHTML || ''; } catch (e) { return; }
    if (html.length > 40) {
      out.shadow.push({
        host: host,
        tag: (host.tagName || '').toLowerCase(),
        html: html.slice(0, %d),
      });
    }
    // A shadow root holds its own elements, and they can hold more roots.
    try {
      for (const node of root.querySelectorAll('*')) {
        if (!node.shadowRoot) continue;
        const key = node.tagName + '|' + out.shadow.length + '|' + node.className;
        if (seen.has(key)) continue;
        seen.add(key);
        walkShadow(node.shadowRoot, node);
      }
    } catch (e) {}
  }

  try {
    for (const node of document.querySelectorAll('*')) {
      if (!node.shadowRoot) continue;
      const key = node.tagName + '|' + out.shadow.length;
      if (seen.has(key)) continue;
      seen.add(key);
      walkShadow(node.shadowRoot, node);
    }
  } catch (e) {}

  const frames = Array.from(document.querySelectorAll('iframe, frame'));
  for (const frame of frames) {
    if (out.frames.length >= %d) break;
    let doc = null;
    try { doc = frame.contentDocument; } catch (e) { doc = null; }
    if (!doc || !doc.body) {
      out.crossOrigin += 1;
      continue;
    }
    out.sameOrigin += 1;
    const text = (doc.body.innerText || '').trim();
    if (text.length < %d) continue;
    out.frames.push({
      src: frame.getAttribute('src') || '',
      title: doc.title || '',
      text: text.slice(0, 2000),
      html: (doc.documentElement.outerHTML || '').slice(0, %d),
    });
  }

  return out;
}
"""


class EmbeddedContent:
    """The parts of a page that a plain `page.content()` cannot see."""

    def __init__(
        self,
        shadow: Optional[List[Dict[str, Any]]] = None,
        frames: Optional[List[Dict[str, Any]]] = None,
        same_origin_frames: int = 0,
        cross_origin_frames: int = 0,
    ):
        self.shadow = shadow or []
        self.frames = frames or []
        self.same_origin_frames = same_origin_frames
        self.cross_origin_frames = cross_origin_frames

    @property
    def anything(self) -> bool:
        return bool(self.shadow or self.frames)

    def to_json(self) -> Dict[str, Any]:
        return {
            "shadow": self.shadow,
            "frames": self.frames,
            "sameOriginFrames": self.same_origin_frames,
            "crossOriginFrames": self.cross_origin_frames,
        }


async def collect_embedded(page: Any) -> EmbeddedContent:
    """Read the shadow roots and same-origin frames the page is showing.

    A browser that cannot be asked is not an error: the page is captured
    either way, just without the parts that were never in the document.
    """
    script = COLLECT_JS % (MAX_SHADOW_ROOTS, MAX_SHADOW_HTML, MAX_FRAMES, MIN_FRAME_CHARS, MAX_FRAME_HTML)
    try:
        found = await page.evaluate(script)
    except Exception:
        return EmbeddedContent()
    if not isinstance(found, dict):
        return EmbeddedContent()
    probed = cast(Dict[str, Any], found)

    def entries(key: str) -> List[Dict[str, Any]]:
        values: Any = probed.get(key)
        if not isinstance(values, list):
            return []
        return [entry for entry in cast(List[Any], values) if isinstance(entry, dict)]

    return EmbeddedContent(
        shadow=entries("shadow"),
        frames=entries("frames"),
        same_origin_frames=int(probed.get("sameOrigin") or 0),
        cross_origin_frames=int(probed.get("crossOrigin") or 0),
    )


def embedded_notes(embedded: EmbeddedContent) -> List[str]:
    """What was hidden inside frames, in words for the prompt.

    Cross-origin frames get a line too. The page shows them, so a clone that
    leaves them out is visibly missing something, and the honest thing is to
    tell the model a placeholder belongs there rather than to pretend the site
    has no map on it.
    """
    if not embedded.anything and not embedded.cross_origin_frames:
        return []

    lines: List[str] = []

    if embedded.shadow:
        hosts = ", ".join(
            f"{entry.get('tag') or 'component'}" for entry in embedded.shadow[:6]
        )
        lines.append(
            f"WEB COMPONENTS: {len(embedded.shadow)} of this page's elements are web "
            f"components whose contents live in shadow DOM ({hosts}). Their markup is "
            "in SHADOW below. A shadow root is closed to a script but open to a "
            "screenshot, so the visible result is known even where the source is not."
        )

    if embedded.frames:
        lines.append(
            f"FRAMES: {len(embedded.frames)} of this page's sections are separate "
            "documents in an <iframe> (maps, players, checkout widgets). Their "
            "contents are in FRAMES below. Reproduce each one as its own block in "
            "the same page - the frame's own chrome, address bar and controls are "
            "not part of it, only what the frame draws."
        )

    if embedded.cross_origin_frames:
        lines.append(
            f"EXTERNAL FRAMES: {embedded.cross_origin_frames} more sections are frames "
            "on another site. Their contents cannot be read, but a screenshot shows "
            "what they look like, so reproduce the visible part and leave a clearly "
            "marked placeholder where the frame itself was."
        )

    return lines


def embedded_blocks(embedded: EmbeddedContent) -> str:
    """The markup of everything hidden, ready to paste into a prompt."""
    if not embedded.anything:
        return ""
    parts: List[str] = []
    for index, entry in enumerate(embedded.shadow, start=1):
        parts.append(
            f"SHADOW {index} (inside <{entry.get('tag') or 'component'}>):\n"
            f"{str(entry.get('html') or '')[:MAX_SHADOW_HTML]}"
        )
    for index, entry in enumerate(embedded.frames, start=1):
        source = entry.get("src") or "(same-origin, no src)"
        parts.append(
            f"FRAME {index} (src={source}, title={entry.get('title') or 'untitled'}):\n"
            f"{str(entry.get('html') or '')[:MAX_FRAME_HTML]}"
        )
    return "\n\n".join(parts)
