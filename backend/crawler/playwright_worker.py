import asyncio
import base64
import json
import sys
from urllib.parse import urljoin, urlparse, urlunparse


def normalize(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", p.query, ""))


def same_domain(url, base):
    ud = urlparse(url).netloc
    bd = urlparse(base).netloc
    return ud == bd or ud.endswith("." + bd)


async def click_selectors(page, selectors, max_clicks=5):
    """Click elements matching any of the selectors."""
    clicked = 0
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:max_clicks]:
                try:
                    if not await el.is_visible():
                        continue
                    await el.click(timeout=1500, force=True)
                    await page.wait_for_timeout(500)
                    clicked += 1
                    if clicked >= max_clicks:
                        return clicked
                except Exception:
                    continue
        except Exception:
            continue
    return clicked


async def scroll_page(page):
    """Scroll down the page to trigger lazy-loaded content."""
    try:
        for i in range(5):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(400)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


async def crawl(start_url, max_pages, max_depth, timeout):
    from playwright.async_api import async_playwright

    if sys.platform == "win32":
        import asyncio as _a
        _a.set_event_loop_policy(_a.WindowsProactorEventLoopPolicy())

    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = "https://" + start_url
        parsed = urlparse(start_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    visited = set()
    pages = []
    explored_selectors = [
        "button:not([disabled])",
        "[role='button']",
        "details > summary",
        "[aria-expanded='false']",
        ".accordion-button",
        ".dropdown-toggle",
        "[data-toggle]",
        "[data-bs-toggle]",
        ".nav-link",
        ".menu-item",
        ".tab",
        "[role='tab']",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        page.set_default_timeout(8000)
        queue = [(start_url, 0)]

        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            norm = normalize(url)
            if norm in visited or depth > max_depth:
                continue
            visited.add(norm)

            print(f"[Worker] -> {url} (depth={depth})", file=sys.stderr, flush=True)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            except Exception as e:
                print(f"[Worker] goto failed: {e}", file=sys.stderr, flush=True)
                continue

            try:
                await page.wait_for_selector("body", timeout=5000)
            except Exception:
                pass

            await page.wait_for_timeout(1500)

            try:
                txt = await page.inner_text("body")
                if len(txt.strip()) < 100:
                    await page.wait_for_timeout(3000)
            except Exception:
                pass

            await scroll_page(page)

            if depth < 2:
                clicked = await click_selectors(page, explored_selectors, max_clicks=8)
                if clicked:
                    print(f"[Worker]   clicked {clicked} elements", file=sys.stderr, flush=True)
                    await page.wait_for_timeout(1000)

                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                await page.wait_for_timeout(500)

            title = await page.title()
            html = await page.content()

            try:
                ss = await page.screenshot(type="png")
                ss_url = "data:image/png;base64," + base64.b64encode(ss).decode()
            except Exception:
                ss_url = ""

            links = []
            for el in await page.query_selector_all("a[href]"):
                try:
                    href = await el.get_attribute("href")
                    if href and not href.startswith("#") and not href.startswith("javascript") and not href.startswith("mailto:"):
                        full = urljoin(url, href)
                        if same_domain(full, base_url):
                            links.append(normalize(full))
                except Exception:
                    continue

            forms = []
            for f in await page.query_selector_all("form"):
                try:
                    fd = {
                        "action": await f.get_attribute("action") or "",
                        "method": (await f.get_attribute("method") or "GET").upper(),
                        "inputs": [],
                    }
                    for inp in await f.query_selector_all("input, textarea, select"):
                        fd["inputs"].append({
                            "type": await inp.get_attribute("type") or "text",
                            "name": await inp.get_attribute("name") or "",
                            "placeholder": await inp.get_attribute("placeholder") or "",
                        })
                    forms.append(fd)
                except Exception:
                    continue

            nav = []
            for el in await page.query_selector_all("nav a[href], header a[href], aside a[href]"):
                try:
                    text = (await el.inner_text()).strip()
                    href = await el.get_attribute("href")
                    if text and href:
                        nav.append({"text": text[:50], "href": urljoin(url, href)})
                except Exception:
                    continue

            imgs = []
            for img in await page.query_selector_all("img[src]"):
                try:
                    src = await img.get_attribute("src")
                    if src:
                        imgs.append(urljoin(url, src))
                except Exception:
                    continue

            ppath = urlparse(url).path.rstrip("/") or "/"
            pages.append({
                "url": url,
                "path": ppath,
                "title": title,
                "html": html,
                "screenshot": ss_url,
                "links": list(set(links)),
                "forms": forms,
                "navigation": nav,
                "images": imgs,
                "depth": depth,
            })
            print(f"[Worker]   {len(pages)}/{max_pages} {ppath} links={len(links)}", file=sys.stderr, flush=True)

            for link in links:
                if link not in visited:
                    queue.append((link, depth + 1))

        await browser.close()
        print(f"[Worker] Done. Total pages: {len(pages)}", file=sys.stderr, flush=True)

    return pages


if __name__ == "__main__":
    data = json.loads(sys.argv[1])
    result = asyncio.run(
        crawl(data["url"], data["max_pages"], data["max_depth"], data["timeout"])
    )
    print(json.dumps(result))
