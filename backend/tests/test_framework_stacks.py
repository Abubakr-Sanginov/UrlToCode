import re

from prompts.framework_stacks import (
    build_route_map,
    clean_component_output,
    is_usable_component,
    route_for_path,
    wrap_component_preview,
)


def test_route_for_path_matches_frontend_layout():
    assert route_for_path("/") == "/"
    assert route_for_path("") == "/"
    assert route_for_path("/About Us/") == "/about-us"
    assert route_for_path("/blog/Post_1?x=1#top") == "/blog/post-1"


def test_build_route_map_keeps_first_path():
    assert build_route_map(["/", "/about/", "/about"]) == {"/": "/", "/about": "/about"}


def test_clean_component_output_strips_fences_and_narration():
    raw = 'Here is the page:\n```tsx\n"use client";\nexport default function Page() {\n  return <p/>;\n}\n```\nDone.'
    cleaned = clean_component_output(raw)

    assert cleaned.startswith('"use client";')
    assert cleaned.endswith("}")
    assert is_usable_component(cleaned)


def test_documents_and_truncated_answers_are_not_components():
    assert not is_usable_component("I'll build the header first.")
    assert not is_usable_component("<!DOCTYPE html><html>export default</html>")
    assert not is_usable_component("export default function Page() {\n  return <main")


def test_preview_embeds_source_that_survives_script_close_tags():
    component = 'export default function Page() {\n  return <p>{"</SCRIPT>"}</p>;\n}'
    document = wrap_component_preview(component, "react_tailwind", "/", "A & B")

    match = re.search(
        r'<script type="text/plain" id="urltocode-source" data-framework="react" '
        r'data-route="/">(.*?)</script>',
        document,
        re.DOTALL,
    )
    assert match
    assert re.sub(r"<\\/(script)", r"</\1", match.group(1), flags=re.I) == component
    assert "<title>A &amp; B</title>" in document


def test_build_head_turns_design_into_base_css():
    from prompts.framework_stacks import build_head

    head = build_head(
        {"lang": "ru", "description": "Desc", "favicon": "https://x.com/f.ico"},
        {
            "bodyFont": "Inter",
            "headingFont": "Playfair Display",
            "backgroundColor": "#0f172a",
            "textColor": "#ffffff; } body { display:none",
            "fontLinks": ["https://fonts.googleapis.com/css2?family=Inter"],
        },
    )

    assert head["lang"] == "ru"
    assert "font-family: 'Inter', sans-serif;" in head["baseCss"]
    assert "background-color: #0f172a;" in head["baseCss"]
    # A value that could break out of the rule is dropped.
    assert "display:none" not in head["baseCss"]
    assert "'Playfair Display'" in head["baseCss"]


def test_preview_carries_head_as_json_and_tags():
    import json

    from prompts.framework_stacks import build_head

    head = build_head(
        {"favicon": "https://x.com/f.ico", "description": "</script>"},
        {"fontLinks": ["https://fonts.googleapis.com/css2?family=Inter"]},
    )
    document = wrap_component_preview(
        "export default function Page() {\n  return <p/>;\n}", "nextjs_tailwind", "/", "T", head
    )

    match = re.search(
        r'<script type="application/json" id="urltocode-head">(.*?)</script>', document, re.S
    )
    assert match
    assert json.loads(match.group(1)) == head
    assert '<link rel="icon" href="https://x.com/f.ico">' in document
    assert 'href="https://fonts.googleapis.com/css2?family=Inter"' in document
