from typing import Any, Dict, List
from crawler.crawler import CrawlResult


def build_page_prompt(
    page_data: Dict[str, Any],
    stack: str,
    generate_database: bool = True,
) -> List[Dict[str, str]]:
    page_path = page_data.get("path", "/")
    page_title = page_data.get("title", "Page")
    page_html = page_data.get("html", "")
    navigation = page_data.get("navigation", [])
    forms = page_data.get("forms", [])

    nav_items = "\n".join(
        f'  - text: "{item.get("text", "")}", href: "{item.get("href", "")}"'
        for item in navigation[:15]
    )

    forms_info = ""
    if forms:
        forms_info = "\n\nForms found on this page:\n"
        for i, form in enumerate(forms):
            inputs = "\n".join(
                f'    - {inp.get("name", "unnamed")} ({inp.get("type", "text")})'
                for inp in form.get("inputs", [])
            )
            forms_info += f"""
  Form {i + 1}:
    Action: {form.get("action", "N/A")}
    Method: {form.get("method", "GET")}
    Inputs:
{inputs}
"""

    system_prompt = f"""You are an expert frontend developer recreating a webpage as a SINGLE, SELF-CONTAINED HTML file.

PAGE TO RECREATE:
- Path: {page_path}
- Title: {page_title}

NAVIGATION:
{nav_items if nav_items else "No navigation found"}
{forms_info}

ORIGINAL HTML (for reference - recreate the design, do NOT copy):
{page_html[:20000]}

CRITICAL OUTPUT RULES:
1. Return ONLY one complete, self-contained HTML file
2. Use Tailwind CSS via CDN: <script src="https://cdn.tailwindcss.com"></script>
3. Use Alpine.js or vanilla JS for interactivity: <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
4. Use Lucide icons via CDN: <script src="https://unpkg.com/lucide@latest"></script>
5. Use placeholder images: https://placehold.co/600x400?text=Image
6. DO NOT use markdown code blocks (no ```html)
7. DO NOT use create_file directives
8. DO NOT add any explanation or comments outside the HTML
9. Start directly with <!DOCTYPE html>
10. Include all CSS in <style> tags and all JS in <script> tags within the HTML
11. Make it responsive (mobile-friendly)
12. Match the original design, layout, colors, typography as closely as possible
13. Include a working navigation bar with links to the other pages
14. Make all buttons and links functional (use # for placeholder links)
15. Recreate any forms found on the page

The output must be ready to save as a single .html file and open in a browser."""

    return [{"role": "system", "content": system_prompt}]


def build_database_schema_prompt(
    crawl_result: CrawlResult,
    stack: str,
) -> str:
    pages_summary = []
    for page in crawl_result.pages:
        pages_summary.append(
            f"- {page.path} ({page.title}): {len(page.forms)} forms, {len(page.images)} images"
        )

    pages_list = "\n".join(pages_summary)

    forms_summary = []
    for page in crawl_result.pages:
        for form in page.forms:
            inputs = ", ".join(
                f'{inp.get("name", "unnamed")}({inp.get("type", "text")})'
                for inp in form.get("inputs", [])
            )
            forms_summary.append(
                f"- Form on {page.path}: method={form.get('method', 'GET')}, inputs=[{inputs}]"
            )

    forms_list = "\n".join(forms_summary) if forms_summary else "No forms found"

    return f"""Generate a database schema for this website.

PAGES:
{pages_list}

FORMS:
{forms_list}

Return ONLY a SQL CREATE TABLE statements block. No markdown, no explanations, no code fences.
Start directly with CREATE TABLE."""


def build_project_structure_prompt(
    crawl_result: CrawlResult,
    stack: str,
    generate_database: bool = True,
    generate_auth: bool = True,
) -> str:
    pages_summary = []
    for page in crawl_result.pages:
        pages_summary.append(
            f"- {page.path} ({page.title}): depth={page.depth}"
        )

    pages_list = "\n".join(pages_summary)

    nav_items = []
    for page in crawl_result.pages:
        for item in page.navigation:
            nav_items.append(item)

    nav_list = "\n".join(
        f'  - text: "{item.get("text", "")}", href: "{item.get("href", "")}"'
        for item in nav_items[:20]
    )

    return f"""You are an expert full-stack architect. Based on the crawled website, generate a complete, self-contained index.html landing page.

CRAWLED PAGES:
{pages_list}

NAVIGATION:
{nav_list if nav_list else "  No navigation found"}

DESIGN TOKENS:
- Colors: {str(crawl_result.design_tokens.get("colors", []))[:200]}
- Fonts: {str(crawl_result.design_tokens.get("fonts", []))[:200]}

TASK: Generate a SINGLE, SELF-CONTAINED index.html file that:
1. Serves as a landing/portal page for the recreated site
2. Has a navigation menu linking to all discovered pages
3. Uses Tailwind CSS via CDN
4. Uses Alpine.js for interactivity
5. Uses Lucide icons
6. Has a modern, clean design matching the original site's style
7. Shows a card/grid of all pages with their titles
8. Is fully responsive

CRITICAL OUTPUT RULES:
- Return ONLY the HTML code
- NO markdown code blocks (no ```html)
- NO create_file directives
- NO explanations or comments outside the HTML
- Start directly with <!DOCTYPE html>
- Include Tailwind CDN, Alpine.js CDN, and Lucide CDN
- All CSS in <style>, all JS in <script> tags
- Ready to save as index.html and open in a browser

The output must be ONE complete HTML file."""