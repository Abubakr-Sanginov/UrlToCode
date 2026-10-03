from typing import Any

from prompts.html_compact import compact_html
from prompts.url_to_code_prompts import (
    PAGE_HTML_BUDGET,
    build_database_schema_prompt,
    build_page_prompt,
    build_link_map,
    build_project_structure_prompt,
    page_filename,
)
from crawler.crawler import CrawlPage, CrawlResult


def test_compact_html_drops_scripts_styles_and_noise():
    html = """
    <html><head>
      <style>.a{color:red}.b{color:blue}</style>
      <script>var x = 1; /* lots of code */</script>
    </head><body>
      <div data-testid="wrapper" onclick="track()" aria-hidden="false" class="css-1a2b3c4 flex">
        <h1>Hello</h1>
        <!-- a comment -->
      </div>
    </body></html>
    """
    out = compact_html(html)

    assert "<script" not in out
    assert "<style" not in out
    assert "color:red" not in out
    assert "data-testid" not in out
    assert "onclick" not in out
    assert "a comment" not in out
    # Structural signal survives.
    assert "Hello" in out
    assert "<h1>" in out
    # Utility classes are kept, generated hashes are not.
    assert "flex" in out
    assert "css-1a2b3c4" not in out


def test_compact_html_collapses_repeated_siblings():
    rows = "".join(
        f'<li class="row"><a href="/p/{i}">Item {i}</a></li>' for i in range(30)
    )
    out = compact_html(f"<html><body><ul>{rows}</ul></body></html>")

    assert "Item 0" in out
    assert "Item 29" not in out
    assert "similar items" in out
    # Collapsing must be a large win on feed-shaped markup.
    assert len(out) < 600


def test_compact_html_respects_budget_and_marks_truncation():
    # Vary class names so sibling-collapsing cannot absorb the content and the
    # budget cap is what actually bounds the output.
    html = "<html><body>" + "".join(
        f'<section class="s{i}"><h2>Section {i}</h2><p>{"body text " * 40}</p></section>'
        for i in range(200)
    ) + "</body></html>"

    out = compact_html(html, max_chars=1500)

    assert out.endswith("<!-- truncated -->")
    assert len(out) <= 1500 + len("\n<!-- truncated -->")


def test_compact_html_skips_truncation_marker_when_under_budget():
    out = compact_html("<html><body><h1>Small</h1></body></html>", max_chars=1500)

    assert "truncated" not in out
    assert "Small" in out


def test_compact_html_handles_empty_and_unparseable_input():
    assert compact_html("") == ""
    assert compact_html("   ") == ""
    # Plain text is not markup, but must not raise.
    assert "just text" in compact_html("just text")


def test_compact_html_strips_data_uris():
    html = '<html><body><img src="data:image/png;base64,' + "A" * 5000 + '" alt="logo"></body></html>'
    out = compact_html(html)

    assert "data:..." in out
    assert "A" * 100 not in out
    assert 'alt="logo"' in out


def _page(
    path: str = "/",
    html: str = "<html><body><h1>Hi</h1></body></html>",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "url": f"https://example.com{path}",
        "path": path,
        "title": "Example",
        "html": html,
        "navigation": [{"text": f"Link {i}", "href": f"/l{i}"} for i in range(40)],
        "forms": [
            {
                "action": "/subscribe",
                "method": "POST",
                "inputs": [
                    {"name": f"f{i}", "type": "text", "placeholder": ""}
                    for i in range(30)
                ],
            }
            for _ in range(10)
        ],
        "images": [],
        "depth": 0,
        **extra,
    }


def test_build_page_prompt_is_compact_and_bounded():
    # A heavy page: lots of script/style bulk plus a long repeated feed.
    bulk = "<script>" + ("x=1;" * 20000) + "</script>"
    feed = "".join(
        f'<article class="post"><h2>Post {i}</h2><p>text {i}</p></article>'
        for i in range(300)
    )
    raw = f"<html><head>{bulk}</head><body>{feed}</body></html>"

    messages = build_page_prompt(_page(html=raw), "html_tailwind")
    prompt = messages[0]["content"]

    assert messages[0]["role"] == "system"
    # Prompt must be far smaller than the source page.
    assert len(prompt) < len(raw) / 10
    # Budget is respected with room for the surrounding instructions.
    assert len(prompt) < PAGE_HTML_BUDGET + 2000
    # Script bulk never reaches the model.
    assert "x=1;x=1;" not in prompt
    # Navigation and form lists are capped rather than dumped wholesale.
    assert "Link 39" not in prompt
    assert "f29" not in prompt
    # Core instructions survive.
    assert "<!DOCTYPE html>" in prompt
    assert "cdn.tailwindcss.com" in prompt


def test_build_page_prompt_handles_missing_fields():
    prompt = build_page_prompt({}, "html_tailwind")[0]["content"]

    assert "NAV: none" in prompt
    assert "FORMS:" not in prompt


def test_measured_widths_reach_the_prompt():
    page = _page(
        viewport_screenshots={
            "desktop": {"file": "d.jpg", "width": 1440, "height": 900},
            "mobile": {"file": "m.jpg", "width": 375, "height": 2400},
        },
        layout_notes=["On mobile the page is 2.7x taller: columns stack."],
    )

    prompt = build_page_prompt(page, "html_tailwind")[0]["content"]

    assert "RESPONSIVE" in prompt
    assert "mobile 375px" in prompt
    assert "columns stack" in prompt
    assert "media queries" in prompt


def test_no_widths_means_no_invented_mobile_layout():
    # Without a measurement the honest instruction is a sensible responsive
    # page, not a claim about what the original does at 375px.
    prompt = build_page_prompt(_page(), "html_tailwind")[0]["content"]

    assert "RESPONSIVE" not in prompt
    assert "375" not in prompt


def test_one_width_is_not_enough_to_describe_a_responsive_layout():
    page = _page(
        viewport_screenshots={"desktop": {"file": "d.jpg", "width": 1440, "height": 900}},
        layout_notes=["everything matches"],
    )

    assert "RESPONSIVE" not in build_page_prompt(page, "html_tailwind")[0]["content"]


def test_captured_states_reach_the_prompt_with_their_trigger():
    page = _page(
        states=[
            {
                "name": "Open menu",
                "label": "Menu",
                "selector": "button",
                "screenshot": "m.jpg",
            }
        ]
    )

    prompt = build_page_prompt(page, "html_tailwind")[0]["content"]

    assert "STATES" in prompt
    assert "Open menu" in prompt
    assert "Menu" in prompt
    assert "reveals nothing" in prompt


def test_no_captured_states_means_no_invented_behaviour():
    # Without a capture the page has no evidence of a menu or a dialog, and
    # inventing one is worse than leaving the clone faithful to what loads.
    prompt = build_page_prompt(_page(), "html_tailwind")[0]["content"]

    assert "STATES" not in prompt


def _crawl_result(page_count: int = 40) -> CrawlResult:
    pages = [
        CrawlPage(
            url=f"https://example.com/p{i}",
            path=f"/p{i}",
            title=f"Page {i}",
            html="<html><body><h1>x</h1></body></html>",
            navigation=[{"text": f"Nav {i}", "href": f"/p{i}"}],
            forms=[
                {
                    "action": "/a",
                    "method": "POST",
                    "inputs": [{"name": "email", "type": "email"}],
                }
            ],
            depth=i % 3,
        )
        for i in range(page_count)
    ]
    return CrawlResult(
        base_url="https://example.com",
        pages=pages,
        design_tokens={
            "colors": [f"#{i:06x}" for i in range(40)],
            "fonts": [f"Font {i}" for i in range(20)],
        },
    )


def test_structure_and_schema_prompts_cap_large_crawls():
    result = _crawl_result(page_count=40)

    structure = build_project_structure_prompt(result, "html_tailwind")
    schema = build_database_schema_prompt(result, "html_tailwind")

    # Page lists are capped, not unbounded in crawl size.
    assert "/p0" in structure
    assert "/p39" not in structure
    assert "/p39" not in schema
    # Design tokens are sampled, not dumped.
    assert "Font 19" not in structure
    # Both prompts stay small enough for a low-context free model.
    assert len(structure) < 3000
    assert len(schema) < 3000
    assert schema.rstrip().endswith("Start at CREATE TABLE.")


def test_no_forms_reported_as_none():
    result = CrawlResult(
        base_url="https://example.com",
        pages=[CrawlPage(url="https://example.com/", path="/", title="T")],
    )

    assert "none" in build_database_schema_prompt(result, "html_tailwind")


def test_stack_selection_changes_output_contract():
    page = _page()

    react = build_page_prompt(page, "react_tailwind")[0]["content"]
    plain_css = build_page_prompt(page, "html_css")[0]["content"]
    bootstrap = build_page_prompt(page, "bootstrap")[0]["content"]
    vue = build_page_prompt(page, "vue_tailwind")[0]["content"]

    nextjs = build_page_prompt(page, "nextjs_tailwind")[0]["content"]

    assert "React 18 page component" in react and "<!DOCTYPE" not in react
    assert "Next.js 14 App Router" in nextjs and '"use client";' in nextjs
    assert "export default function Page()" in nextjs
    assert "No CSS framework" in plain_css
    assert "cdn.tailwindcss.com" not in plain_css
    assert "Bootstrap 5" in bootstrap and "No Tailwind" in bootstrap
    assert "vue.global.js" in vue


def test_unknown_stack_falls_back_to_html_tailwind():
    prompt = build_page_prompt(_page(), "not_a_stack")[0]["content"]

    assert "cdn.tailwindcss.com" in prompt


def test_generate_auth_and_database_toggle_structure_requirements():
    result = _crawl_result(page_count=3)

    with_extras = build_project_structure_prompt(
        result, "html_tailwind", generate_database=True, generate_auth=True
    )
    without_extras = build_project_structure_prompt(
        result, "html_tailwind", generate_database=False, generate_auth=False
    )

    # generateAuth used to be accepted and then ignored entirely.
    assert "Sign in" in with_extras
    assert "Data model" in with_extras
    assert "Sign in" not in without_extras
    assert "Data model" not in without_extras


def test_page_filename_matches_frontend_project_layout():
    assert page_filename("/") == "index.html"
    assert page_filename("/about/") == "about.html"
    assert page_filename("/Blog/Post 1") == "blog-post-1.html"


def test_page_prompt_uses_real_images_and_local_links():
    html = """<html><body>
      <nav><a href="/about">About</a><a href="https://other.com/x">Out</a></nav>
      <img src="/img/logo.png" alt="Logo">
    </body></html>"""
    page: dict[str, Any] = {
        "url": "https://www.example.com/",
        "path": "/",
        "title": "Home",
        "html": html,
        "images": ["https://www.example.com/img/logo.png"],
        "navigation": [{"text": "About", "href": "https://example.com/about/"}],
    }
    link_map = build_link_map(["/", "/about"])
    prompt = build_page_prompt(page, "html_tailwind", link_map=link_map)[0]["content"]

    # Relative image sources are dead once the clone is saved to disk.
    assert 'src="https://www.example.com/img/logo.png"' in prompt
    # Crawled pages point at their local files; outside links stay absolute.
    assert 'href="about.html"' in prompt
    assert "About -> about.html" in prompt
    assert 'href="https://other.com/x"' in prompt
    assert "/about -> about.html" in prompt
