"""Stacks whose clone is a real framework project instead of loose HTML files.

For these stacks every crawled page is generated as one TSX page component.
The rest of the pipeline (preview iframe, commits, edits) works on HTML, so
each component travels inside a small preview document that renders it with
React + Babel from a CDN. The component source is embedded verbatim in that
document; the frontend pulls it back out when it writes the project
(frontend/src/lib/frameworkProject.ts), so both ends must agree on
`SOURCE_SCRIPT_ID` and the attribute layout of the source tag.
"""

import html
import json
import re
from typing import Any, Dict, Iterable, List

from babel_cdn import PINNED_BABEL_STANDALONE_URL

NEXTJS_STACK = "nextjs_tailwind"
REACT_STACK = "react_tailwind"

# Stack -> framework id written into the preview document.
FRAMEWORK_STACKS: Dict[str, str] = {
    NEXTJS_STACK: "nextjs",
    REACT_STACK: "react",
}

SOURCE_SCRIPT_ID = "urltocode-source"


def is_framework_stack(stack: str) -> bool:
    return stack in FRAMEWORK_STACKS


def route_for_path(path: str) -> str:
    """URL route a crawled page gets in the generated project.

    Mirrors `routeForPath` in frontend/src/lib/frameworkProject.ts: the page
    lives at app/<route>/page.tsx (Next.js) or is matched on this pathname
    (React), so links written into one page reach the others.
    """
    cleaned = (path or "").split("?", 1)[0].split("#", 1)[0]
    segments = [
        re.sub(r"[^a-z0-9]+", "-", segment.lower()).strip("-")
        for segment in cleaned.split("/")
    ]
    segments = [segment for segment in segments if segment]
    return "/" + "/".join(segments)


def build_route_map(paths: Iterable[str]) -> Dict[str, str]:
    """Map every crawled page path to its route (first one wins)."""
    route_map: Dict[str, str] = {}
    for path in paths:
        route_map.setdefault(path.rstrip("/") or "/", route_for_path(path))
    return route_map


_FRAMEWORK_RULES: Dict[str, str] = {
    "nextjs": """- Next.js 14 App Router page, TypeScript, Tailwind CSS classes for all styling.
- The file is app/<route>/page.tsx. The FIRST line is exactly: "use client";
- Allowed imports ONLY: `import Link from "next/link";` and hooks from "react".
- Internal links: <Link href="/route">; external links: <a href="https://...">.""",
    "react": """- React 18 page component, TypeScript, Tailwind CSS classes for all styling.
- The file is src/pages/<Name>.tsx in a Vite project.
- Allowed imports ONLY: hooks from "react" (e.g. `import { useState } from "react";`).
- Internal links: <a href="/route">; external links: <a href="https://...">.""",
}


def framework_output_rules(stack: str) -> str:
    """Output contract for one generated page component."""
    framework = FRAMEWORK_STACKS[stack]
    return f"""OUTPUT RULES:
- Emit ONE complete .tsx file, nothing else. No markdown fences, no commentary.
{_FRAMEWORK_RULES[framework]}
- The file has exactly one `export default function Page()` returning the page JSX.
- No <html>, <head> or <body>: return only what is inside <body>.
- Valid JSX: className (not class), htmlFor, self-closing void tags (<img />, <br />, <input />),
  style as objects (style={{{{ color: "#111" }}}}), escape braces in text.
- No other packages: no lucide-react, no next/image, no icon libraries. Draw icons as inline <svg>.
- Colors, fonts and sizes the Tailwind palette lacks: use arbitrary values (bg-[#0f172a], text-[15px]).
- Images: plain <img> with the original image URLs exactly as given (they are
  absolute). Use https://placehold.co/600x400?text=Image only where the source has none.
- Media: every <video> in STRUCTURE is reproduced as a <video> with the same src,
  poster and autoPlay/muted/loop/playsInline props. An element with data-bg-image has
  that URL as its CSS background image: style={{{{ backgroundImage: "url(...)" }}}}.
- Links: use the routes from LOCAL PAGES for crawled pages; keep other hrefs exactly as given.
- Responsive."""


def clean_component_output(text: str) -> str:
    """Pull the component file out of whatever the model wrapped it in."""
    text = text.strip()

    blocks = re.findall(r"```(?:[a-zA-Z]*)\s*\n(.*?)```", text, re.DOTALL)
    if blocks:
        text = max(blocks, key=len).strip()
    elif text.startswith("```"):
        # An unterminated fence: the answer was cut off mid-block.
        text = text.split("\n", 1)[1] if "\n" in text else ""

    # Drop narration before the code ("Here is the page:").
    start = re.search(r"""^\s*(?:["']use client["']|import\s|export\s|const\s|function\s)""", text, re.MULTILINE)
    if start:
        text = text[start.start() :]
    return text.strip()


def is_usable_component(text: str) -> bool:
    """True when `text` is a page component rather than commentary."""
    if not text or "export default" not in text:
        return False
    # A document instead of a component means the stack rules were ignored.
    lowered = text.lower()
    if "<!doctype html" in lowered or "<html" in lowered:
        return False
    # An answer cut off mid-way leaves the JSX unbalanced.
    return text.rstrip().endswith(("}", ";", ")"))


def _page_title(page_title: str) -> str:
    return html.escape(page_title or "Page", quote=False)


# Renders the embedded component in the browser. Imports are replaced by
# globals (React hooks, a Link shim) and root-relative links are sent to the
# sibling preview files, so the preview navigates like the saved static copy.
_PREVIEW_RUNTIME = r"""(function () {
  var mount = document.getElementById("root");
  function fail(error) {
    mount.innerHTML = "";
    var pre = document.createElement("pre");
    pre.style.cssText = "white-space:pre-wrap;color:#b91c1c;padding:16px;font:13px monospace";
    pre.textContent = "Preview failed: " + (error && error.message ? error.message : error);
    mount.appendChild(pre);
  }
  try {
    var source = document.getElementById("urltocode-source").textContent
      .replace(/<\\\/(script)/gi, "</$1");
    var code = source
      .replace(/^\s*["']use client["'];?/m, "")
      .replace(/^\s*import\s[\s\S]*?from\s*["'][^"']+["'];?[ \t]*$/gm, "")
      .replace(/^\s*import\s*["'][^"']+["'];?[ \t]*$/gm, "")
      .replace(/export\s+default\s+/, "window.__UrlToCodePage = ")
      .replace(/^(\s*)export\s+(?=(const|let|var|function|type|interface|class)\s)/gm, "$1");
    var prelude =
      "var { useState, useEffect, useRef, useMemo, useCallback, useReducer, useContext, createContext, Fragment } = React;" +
      "var Link = function (props) { var rest = Object.assign({}, props); delete rest.prefetch; delete rest.replace; delete rest.scroll; return React.createElement('a', rest); };";
    var compiled = Babel.transform(prelude + "\n" + code, {
      filename: "page.tsx",
      presets: [["typescript", { isTSX: true, allExtensions: true }], ["react", { runtime: "classic" }]],
    }).code;
    new Function(compiled)();
    ReactDOM.createRoot(mount).render(React.createElement(window.__UrlToCodePage));
  } catch (error) {
    fail(error);
  }
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href^='/']");
    if (!link) return;
    var route = link.getAttribute("href").split("#")[0].split("?")[0];
    var slug = route.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    event.preventDefault();
    window.location.href = (slug ? slug : "index") + ".html";
  });
})();"""


HEAD_SCRIPT_ID = "urltocode-head"

_CSS_VALUE_RE = re.compile(r"^[#\w\s\-.,()%]+$")


def _css_font(name: str) -> str:
    # Font names come from the crawled site; nothing may end the string or
    # the <style> element it lands in.
    return "'" + re.sub(r"[\\'<>]", "", name) + "'"


def build_head(meta: Dict[str, Any], design: Dict[str, Any]) -> Dict[str, Any]:
    """Site-wide head settings for a framework project's layout.

    Travels as JSON in every preview document; the frontend writes it into
    app/layout.tsx (Next.js) or index.html (React) and the global stylesheet.
    """
    rules: List[str] = []
    body_font = str(design.get("bodyFont") or "")
    if body_font:
        rules.append(f"font-family: {_css_font(body_font)}, sans-serif;")
    for prop, key in (("background-color", "backgroundColor"), ("color", "textColor")):
        value = str(design.get(key) or "")
        if value and _CSS_VALUE_RE.match(value):
            rules.append(f"{prop}: {value};")
    base_css = "body { " + " ".join(rules) + " }" if rules else ""
    heading_font = str(design.get("headingFont") or "")
    if heading_font and heading_font != body_font:
        base_css += f"\nh1, h2, h3 {{ font-family: {_css_font(heading_font)}, sans-serif; }}"

    return {
        "lang": str(meta.get("lang") or ""),
        "description": str(meta.get("description") or "")[:300],
        "favicon": str(meta.get("favicon") or ""),
        "fontLinks": [str(link) for link in design.get("fontLinks", [])][:4],
        "baseCss": base_css.strip(),
    }


def _head_tags(head: Dict[str, Any]) -> str:
    tags: List[str] = []
    if head.get("description"):
        tags.append(f'<meta name="description" content="{html.escape(head["description"])}">')
    if head.get("favicon"):
        tags.append(f'<link rel="icon" href="{html.escape(head["favicon"])}">')
    for link in head.get("fontLinks", []):
        tags.append(f'<link rel="stylesheet" href="{html.escape(link)}">')
    if head.get("baseCss"):
        tags.append(f"<style>{head['baseCss']}</style>")
    # The layout is rebuilt from this, not from the tags above.
    data = json.dumps(head).replace("</", "<\\/")
    tags.append(f'<script type="application/json" id="{HEAD_SCRIPT_ID}">{data}</script>')
    return "\n  ".join(tags)


def wrap_component_preview(
    component: str,
    stack: str,
    route: str,
    page_title: str,
    head: Dict[str, Any] | None = None,
) -> str:
    """HTML document that previews `component` and carries its source."""
    framework = FRAMEWORK_STACKS[stack]
    head = head or build_head({}, {})
    lang = html.escape(head.get("lang") or "en")
    # The source sits in a script element, which ends at the first "</script".
    embedded = re.sub(
        r"</(script)", lambda m: "<\\/" + m.group(1), component, flags=re.IGNORECASE
    )
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_page_title(page_title)}</title>
  {_head_tags(head)}
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="{PINNED_BABEL_STANDALONE_URL}"></script>
</head>
<body>
  <div id="root"></div>
  <script type="text/plain" id="{SOURCE_SCRIPT_ID}" data-framework="{framework}" data-route="{html.escape(route)}">{embedded}</script>
  <script>
{_PREVIEW_RUNTIME}
  </script>
</body>
</html>"""
