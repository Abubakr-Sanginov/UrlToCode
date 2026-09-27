import asyncio
import base64
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse

# Run as a script (`python crawler/playwright_worker.py`), so the backend
# package root is not on the path by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CRAWLER_HEADLESS  # noqa: E402  (path set up above)
from llm_http import (  # noqa: E402  (path set up above)
    LlmConfig,
    ProviderError,
    complete,
    read_worker_params,
)


def normalize(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", p.query, ""))


def same_domain(url: str, base: str) -> bool:
    ud: str = urlparse(url).netloc
    bd: str = urlparse(base).netloc
    return ud == bd or ud.endswith("." + bd)


async def _neutralize_navigation(page):
    """Prevent clicks from navigating or submitting forms.

    Clicking real links or submit buttons navigates away (or posts the form),
    so every later field (title, html, links, forms) would describe a
    different page under the original URL. A capture-phase click handler
    cancels those default actions while leaving dropdown/tab clicks working.

    The handler honours window.__neutralize_nav so LLM-guided exploration
    can temporarily re-enable navigation to observe search result pages.
    """
    await page.evaluate(
        """
        window.__neutralize_nav = true;
        document.addEventListener('click', (e) => {
            if (!window.__neutralize_nav) return;
            const t = e.target;
            if (t && t.closest && (t.closest('a') || t.closest('form'))) {
                e.preventDefault();
            }
        }, true);
        """
    )


SIGN_IN_TEXT_RE = re.compile(
    r"^(sign\s?in|sign\s?up|log\s?in|register|войти|вход|регистрац|"
    r"anmelden|connexion|iniciar sesi)",
    re.IGNORECASE,
)


async def click_selectors(page: Any, selectors: List[str], max_clicks: int = 5) -> int:
    """Click elements matching any of the selectors, staying on this page.

    Single-page apps navigate from JavaScript, which preventDefault cannot
    stop, so a click can silently move the browser elsewhere (YouTube ends up
    on the Google sign-in page). Everything captured afterwards would then
    describe that other page under this URL, so a click that navigates ends
    the click phase and the original URL is restored.
    """
    start_url = page.url
    clicked = 0

    async def back_on_page() -> bool:
        if normalize(page.url) == normalize(start_url):
            return True
        print(
            f"[Worker]   click navigated to {page.url}; returning to {start_url}",
            file=sys.stderr, flush=True,
        )
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception as e:
            print(f"[Worker]   failed to return: {e}", file=sys.stderr, flush=True)
        return False

    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:max_clicks]:
                try:
                    if not await el.is_visible():
                        continue
                    label = (
                        (await el.inner_text())
                        or (await el.get_attribute("aria-label"))
                        or ""
                    ).strip()
                    if SIGN_IN_TEXT_RE.match(label):
                        # Sign-in controls lead off-site; the page behind them
                        # is not part of this crawl.
                        continue
                    await el.click(timeout=1500, force=True)
                    await page.wait_for_timeout(500)
                    clicked += 1
                    if not await back_on_page():
                        return clicked
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


# ---------------------------------------------------------------------------
# LLM-guided exploration
# ---------------------------------------------------------------------------

_EXTRACT_ELEMENTS_JS = """
() => {
    function getSelector(el) {
        if (el.id) return '#' + el.id;
        if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
        const testid = el.getAttribute('data-testid');
        if (testid) return '[data-testid="' + testid + '"]';
        const aria = el.getAttribute('aria-label');
        if (aria) return '[aria-label="' + aria.replace(/"/g, '\\\\\\"') + '"]';
        const cls = typeof el.className === 'string' ? el.className.trim() : '';
        if (cls) {
            const first = cls.split(/\\\\s+/)[0];
            return el.tagName.toLowerCase() + '.' + first;
        }
        return el.tagName.toLowerCase();
    }

    const results = [];
    const seen = new Set();

    function add(el, kind) {
        const sel = getSelector(el);
        if (seen.has(sel)) return;
        seen.add(sel);
        const text = (el.textContent || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 60);
        results.push({
            kind: kind,
            selector: sel,
            text: text,
            type: el.type || el.getAttribute('type') || '',
            placeholder: el.placeholder || '',
            name: el.name || '',
            href: el.getAttribute('href') || '',
        });
    }

    // Search inputs — highest priority
    document.querySelectorAll("input[type='search'], [role='searchbox'], input[placeholder*='search' i], input[name*='search' i], input[aria-label*='search' i]").forEach(el => add(el, 'search-input'));

    // Other text inputs
    document.querySelectorAll("input[type='text'], input:not([type])").forEach(el => {
        if (!seen.has(getSelector(el))) add(el, 'text-input');
    });

    // Buttons
    document.querySelectorAll("button:not([disabled]), [role='button']:not([disabled]), input[type='submit'], input[type='button']").forEach(el => add(el, 'button'));

    // Tabs
    document.querySelectorAll("[role='tab'], .tab, .nav-link, .menu-item").forEach(el => add(el, 'tab'));

    // Accordions / expandable
    document.querySelectorAll("[aria-expanded='false'], details > summary, .accordion-button, .dropdown-toggle, [data-toggle], [data-bs-toggle]").forEach(el => add(el, 'expandable'));

    // Links (informational only — LLM should NOT navigate through these)
    document.querySelectorAll("nav a[href], header a[href]").forEach(el => add(el, 'nav-link'));

    return results.slice(0, 35);
}
"""


def _build_exploration_prompt(title, url, elements, page_text):
    """Build the prompt that asks the LLM what to interact with."""
    elements_text = json.dumps(elements, indent=2, ensure_ascii=False)
    return f"""You are exploring a webpage to understand its content and functionality.

Page title: "{title}"
URL: {url}

Page content (first 500 chars):
{page_text[:500]}

Interactive elements found on the page:
{elements_text}

Your task: decide what to interact with to discover MORE content that is currently hidden.

LOOK AT THE PAGE FIRST. Work out what kind of site this is and what its main
input field is for, then act accordingly:

SEARCH / QUERY FIELDS (highest priority):
- A field may be a site search, but it may also be a chat/prompt box, a
  filter, a login field, a newsletter signup or a code/address lookup. The
  placeholder, aria-label, name and surrounding text tell you which.
- Only type into a field whose result is *content worth cloning*: site search,
  filters, query boxes. Never type into login, password, payment or signup
  fields.
- Use a SHORT query (1-2 words) that this specific site would plausibly have
  results for — derive it from the page topic, not from a generic word list.
- Results may appear in three different ways, all of them useful:
  (a) a new results URL, (b) the same URL with the results rendered in place,
  (c) a suggestion/autocomplete dropdown under the field.
  Type the query and we will observe which one happens.

OTHER RULES:
1. Click tabs, accordions, dropdowns and filters to reveal hidden content.
2. Do NOT click regular navigation links — those are crawled separately.
3. Maximum 4 actions, ordered most valuable first.
4. Think step by step: what does this site do? What content is hidden behind
   interactions?

Respond with ONLY valid JSON (no markdown fences, no explanation). Each action
may carry a short "why" describing what you expect to happen:
{{"actions": [{{"type": "type", "selector": "input[name='q']", "value": "music", "why": "site search for videos"}}, {{"type": "click", "selector": "[role='tab']:nth-child(2)", "why": "second tab"}}]}}

If there is nothing worth interacting with, respond: {{"actions": []}}"""


async def _call_llm(llm_config: Dict[str, Any], system_prompt: str) -> Optional[str]:
    """Ask the model what to interact with; None when it cannot answer.

    Exploration is best-effort: a provider failure costs a few interactions,
    never the crawl, so refusals are logged rather than raised.
    """
    cfg = LlmConfig.from_dict(llm_config)
    if not cfg.is_usable:
        return None

    try:
        # Deciding what to click is a small answer; a long budget here only
        # delays a crawl that is already on a deadline.
        result = await complete(
            cfg,
            system_prompt,
            "Return the JSON actions now.",
            max_tokens=2000,
            timeout=90,
        )
    except ProviderError as e:
        print(f"[Worker] LLM refused: {e}", file=sys.stderr, flush=True)
        return None
    except Exception as e:
        print(f"[Worker] LLM call failed: {e}", file=sys.stderr, flush=True)
        return None

    if not result.text:
        print(f"[Worker] LLM gave no actions: {result.empty_reason}", file=sys.stderr, flush=True)
    return result.text


def _parse_actions(response_text: Optional[str]) -> List[Dict[str, Any]]:
    """Parse the LLM JSON response into a list of actions."""
    if not response_text:
        return []
    try:
        text: str = response_text.strip()
        # Strip markdown fences if present
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        # Find the JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed: Any = json.loads(text[start:end + 1])
            actions = parsed.get("actions", [])
            if isinstance(actions, list):
                valid: List[Dict[str, Any]] = []
                a: Any
                for a in actions:
                    if (
                        isinstance(a, dict)
                        and a.get("type") in ("click", "type")
                        and a.get("selector")
                    ):
                        valid.append(a)
                return valid[:5]
    except (ValueError, KeyError) as e:
        print(f"[Worker] Failed to parse LLM actions: {e}", file=sys.stderr, flush=True)
    return []


SENSITIVE_FIELD_RE = re.compile(
    r"password|passwd|email|e-mail|card|cvv|phone|login|sign\s?in|sign\s?up|"
    r"register|subscribe|newsletter|identifier|username|user_?name|account|"
    r"auth|otp|passcode|billing",
    re.IGNORECASE,
)

# Pages whose only inputs are credentials. Sites redirect to these (YouTube
# bounces to accounts.google.com), and the one thing we must not do there is
# type into the form.
AUTH_URL_RE = re.compile(
    r"accounts\.google\.|/signin|/sign-in|/login|/log-in|/register|/signup|/auth",
    re.IGNORECASE,
)


def _looks_sensitive(element: Dict[str, Any]) -> bool:
    """True for fields we must not type into (credentials, signup, payment)."""
    if (element.get("type") or "").lower() in ("password", "email", "tel"):
        return True
    haystack = " ".join(
        str(element.get(k) or "")
        for k in ("selector", "text", "placeholder", "name")
    )
    return bool(SENSITIVE_FIELD_RE.search(haystack))


async def _body_text_len(page: Any) -> int:
    try:
        return int(await page.evaluate(
            "document.body ? document.body.innerText.length : 0"
        ))
    except Exception:
        return 0


async def _wait_for_result(
    page: Any, before_url: str, before_len: int, timeout_ms: int = 8000
) -> str:
    """Wait until the page reacts to an interaction.

    Search results show up in three different shapes: a new results URL, the
    same URL re-rendered in place, or a suggestion dropdown. Polling for
    either a URL change or a substantial body-text change catches all three,
    instead of assuming the URL must change.

    Returns "url", "dom" or "" (nothing observable happened).
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            if normalize(page.url) != normalize(before_url):
                return "url"
            if abs(await _body_text_len(page) - before_len) > 200:
                return "dom"
        except Exception:
            pass
        await page.wait_for_timeout(400)
    return ""


async def _execute_action(page: Any, action: Dict[str, Any]) -> tuple[bool, Optional[str], str]:
    """Execute a single LLM-directed action and observe what it produced.

    Returns (produced_something, new_url, description). `produced_something`
    is true when the page visibly reacted — a navigation OR an in-place DOM
    update — so client-side search is not mistaken for a no-op.
    """
    action_type: str = action.get("type", "")
    selector: str = action.get("selector", "")

    if not selector:
        return False, None, ""

    desc = f"{action_type} on '{selector}'"
    if action_type == "type":
        desc += f" value='{action.get('value', '')}'"
    if action.get("why"):
        desc += f" ({str(action['why'])[:60]})"

    try:
        el = await page.query_selector(selector)
        if not el or not await el.is_visible():
            return False, None, desc + " [not visible]"

        if action_type == "type":
            value = str(action.get("value", "")).strip()
            if not value:
                return False, None, desc + " [empty query]"
            if _looks_sensitive({
                "selector": selector,
                "placeholder": await el.get_attribute("placeholder") or "",
                "name": await el.get_attribute("name") or "",
                "type": await el.get_attribute("type") or "",
            }):
                return False, None, desc + " [skipped: sensitive field]"

            before_url = page.url
            before_len = await _body_text_len(page)

            await el.click(timeout=2000)
            try:
                await el.fill("")
            except Exception:
                pass
            # Type through the keyboard rather than the element handle: it
            # fires the input events live-search listens for, and it keeps
            # working on sites (Wikipedia, most SPAs) that swap the search box
            # for a hydrated one mid-typing, detaching the handle.
            await page.keyboard.type(value, delay=60)
            suggestion = await _wait_for_result(
                page, before_url, before_len, timeout_ms=2500
            )
            if suggestion:
                desc += f" [live {suggestion}]"

            # Submit via Enter — this fires the form submit event, which
            # bypasses the click-phase navigation blocker.
            try:
                await page.keyboard.press("Enter")
            except Exception as e:
                print(f"[Worker] Enter failed: {e}", file=sys.stderr, flush=True)
            observed = await _wait_for_result(page, before_url, before_len)
            if not observed and suggestion:
                # Live results already rendered; Enter changed nothing more.
                observed = suggestion
            desc += f" [observed={observed or 'nothing'}]"
            return bool(observed), page.url, desc

        elif action_type == "click":
            before_url = page.url
            before_len = await _body_text_len(page)
            await el.click(timeout=2000, force=True)
            observed = await _wait_for_result(
                page, before_url, before_len, timeout_ms=5000
            )
            desc += f" [observed={observed or 'nothing'}]"
            return bool(observed), page.url, desc
    except Exception as e:
        print(f"[Worker] Action failed ({desc}): {e}", file=sys.stderr, flush=True)

    return False, None, desc


# Matched as a prefix of a short button label ("Accept all", "Принять все").
CONSENT_TEXT_RE = re.compile(
    "^(accept|agree|i agree|allow|got it|ok|"
    "accept all|accept cookies|alle akzeptieren|akzeptieren|"
    "aceptar|accepter|aceitar|принять|согласен|соглашаюсь|хорошо)",
    re.IGNORECASE,
)


CONSENT_URL_RE = re.compile(r"consent\.|/consent|cookie[-_]?(wall|consent)", re.IGNORECASE)


# Bot-check interstitials ("Just a moment…", "Checking your browser"). Cloning
# one of these produces a blank page, so they are waited out and, if they
# persist, reported instead of being handed to the model as if they were the
# site.
CHALLENGE_TITLE_RE = re.compile(
    "just a moment|checking your browser|attention required|"
    "verifying you are human|Один момент|"
    "проверка браузера",
    re.IGNORECASE,
)


async def _is_challenge_page(page: Any) -> bool:
    try:
        title: str = str(await page.title() or "")
    except Exception:
        return False
    if CHALLENGE_TITLE_RE.search(title):
        return True
    try:
        html: str = str(await page.content() or "")
    except Exception:
        return False
    return "cf-challenge" in html or "challenge-platform" in html


async def wait_out_bot_check(page: Any, max_wait: float = 20.0) -> bool:
    """Give a Cloudflare-style check time to clear itself.

    Returns True if the page is still a challenge afterwards.
    """
    if not await _is_challenge_page(page):
        return False

    print("[Worker] Bot check detected, waiting it out", file=sys.stderr, flush=True)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        await page.wait_for_timeout(2000)
        if not await _is_challenge_page(page):
            print("[Worker] Bot check cleared", file=sys.stderr, flush=True)
            return False
    print("[Worker] Bot check did not clear", file=sys.stderr, flush=True)
    return True


async def dismiss_consent(page: Any, attempts: int = 3) -> bool:
    """Click through a cookie/consent wall before capturing the page.

    Google serves consent.youtube.com instead of YouTube itself, so a crawl
    that ignores the wall clones the dialog rather than the site. Only
    accept-style buttons are clicked, and only one of them. The wall often
    renders a beat after DOMContentLoaded, so a single look misses it.
    """
    for attempt in range(attempts):
        if await _try_dismiss_consent(page):
            return True
        # Only keep waiting while we are demonstrably stuck on a consent wall.
        if attempt + 1 < attempts and CONSENT_URL_RE.search(page.url):
            await page.wait_for_timeout(1500)
            continue
        return False
    return False


async def _try_dismiss_consent(page: Any) -> bool:
    try:
        candidates = await page.query_selector_all(
            "button, [role='button'], input[type='submit'], form a[role='button']"
        )
    except Exception:
        return False

    for el in candidates[:40]:
        try:
            if not await el.is_visible():
                continue
            label = (
                await el.inner_text()
                or await el.get_attribute("aria-label")
                or await el.get_attribute("value")
                or ""
            ).strip()
            # A long label is prose, not a consent button.
            if len(label) > 40 or not CONSENT_TEXT_RE.match(label):
                continue
            before_url = page.url
            before_len = await _body_text_len(page)
            await el.click(timeout=3000)
            changed = await _wait_for_result(page, before_url, before_len, timeout_ms=6000)
            print(
                f"[Worker] Dismissed consent via '{label}' -> {changed or 'no change'}",
                file=sys.stderr, flush=True,
            )
            return bool(changed)
        except Exception:
            continue
    return False


async def _capture_page_data(
    page: Any, url: str, base_url: str, depth: int, capture_screenshots: bool
) -> Dict[str, Any]:
    """Capture all page data: title, html, links, forms, nav, images."""
    try:
        title = await page.title()
    except Exception:
        title = ""

    try:
        html = await page.content()
    except Exception:
        html = ""

    screenshot_url = ""
    if capture_screenshots:
        try:
            ss = await page.screenshot(type="png")
            screenshot_url = "data:image/png;base64," + base64.b64encode(ss).decode()
        except Exception:
            pass

    links: List[str] = []
    try:
        anchors = await page.query_selector_all("a[href]")
        for el in anchors:
            try:
                href = await el.get_attribute("href")
                if href and not href.startswith("#") and not href.startswith("javascript") and not href.startswith("mailto:"):
                    full = urljoin(page.url, href)
                    if same_domain(full, base_url):
                        links.append(normalize(full))
            except Exception:
                continue
    except Exception as e:
        # Silently returning zero links made a broken capture look like a
        # single-page site, so the whole crawl stopped after one page.
        print(f"[Worker] Link extraction failed: {e}", file=sys.stderr, flush=True)

    forms: List[Dict[str, Any]] = []
    try:
        for f in await page.query_selector_all("form"):
            try:
                fd: Dict[str, Any] = {
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
    except Exception:
        pass

    nav: List[Dict[str, str]] = []
    try:
        for el in await page.query_selector_all("nav a[href], header a[href], aside a[href]"):
            try:
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href")
                if text and href:
                    nav.append({"text": text[:50], "href": urljoin(url, href)})
            except Exception:
                continue
    except Exception:
        pass

    imgs: List[str] = []
    try:
        for img in await page.query_selector_all("img[src]"):
            try:
                src = await img.get_attribute("src")
                if src:
                    imgs.append(urljoin(url, src))
            except Exception:
                continue
    except Exception:
        pass

    parsed_url = urlparse(url)
    ppath = parsed_url.path.rstrip("/") or "/"

    return {
        "url": url,
        "path": ppath,
        "title": title,
        "html": html,
        "screenshot": screenshot_url,
        "links": list(set(links)),
        "forms": forms,
        "navigation": nav,
        "images": imgs,
        "depth": depth,
    }


def _fallback_search_action(
    interactive: List[Dict[str, Any]], title: str, base_url: str, page_url: str = ""
) -> Optional[Dict[str, Any]]:
    """Type into the page's search box when the model proposed nothing.

    A model that answers with an empty action list still leaves the most
    valuable interaction on the table: the site's own search. Deriving the
    query from the page title (falling back to the domain label) keeps it
    relevant to this site rather than a canned word.
    """
    if AUTH_URL_RE.search(page_url):
        return None

    # Only a field the page itself presents as search. Any other text input on
    # an unknown page is as likely to be a login or signup box.
    candidates = [
        e for e in interactive
        if e.get("kind") == "search-input" and not _looks_sensitive(e)
    ]
    if not candidates:
        return None

    words = re.findall(r"[^\W\d_]{3,}", title, re.UNICODE)
    query = words[0] if words else ""
    if not query:
        host = urlparse(base_url).netloc.replace("www.", "")
        query = host.split(".")[0] or "search"
    return {
        "type": "type",
        "selector": candidates[0]["selector"],
        "value": query,
        "why": "fallback: site search",
    }


async def _llm_guided_explore(
    page: Any,
    llm_config: Optional[Dict[str, Any]],
    url: str,
    title: str,
    base_url: str,
    depth: int,
    capture_screenshots: bool,
    deadline_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Ask the LLM what to interact with, execute the actions, capture results.

    Returns a list of virtual page dicts discovered through interaction
    (e.g. search-result pages).
    """
    if not llm_config:
        return []

    # Extract interactive elements from the DOM
    try:
        elements: List[Dict[str, Any]] = await page.evaluate(_EXTRACT_ELEMENTS_JS)
    except Exception as e:
        print(f"[Worker] Element extraction failed: {e}", file=sys.stderr, flush=True)
        return []

    if not elements:
        return []

    # Only show interactive elements (not plain nav links) to the LLM
    interactive: List[Dict[str, Any]] = [e for e in elements if e.get("kind") != "nav-link"]
    if not interactive:
        return []

    # Grab a snippet of page text so the LLM understands the site topic
    try:
        page_text: str = await page.inner_text("body")
    except Exception:
        page_text = ""

    prompt = _build_exploration_prompt(title, url, interactive, page_text)
    print(
        f"[Worker] Asking LLM to explore {url} ({len(interactive)} interactive elements)",
        file=sys.stderr, flush=True,
    )

    response_text = await _call_llm(llm_config, prompt)
    actions = _parse_actions(response_text)
    if not response_text:
        print("[Worker] LLM returned no exploration response", file=sys.stderr, flush=True)
    elif not actions:
        print("[Worker] LLM suggested no actions", file=sys.stderr, flush=True)

    if not actions:
        fallback = _fallback_search_action(interactive, title, base_url, page.url)
        if not fallback:
            return []
        print(
            f"[Worker] Falling back to site search: {fallback['selector']} "
            f"value='{fallback['value']}'",
            file=sys.stderr, flush=True,
        )
        actions = [fallback]
    else:
        print(f"[Worker] LLM suggested {len(actions)} actions", file=sys.stderr, flush=True)

    # Let the LLM's actions navigate (search submit, etc.)
    try:
        await page.evaluate("window.__neutralize_nav = false")
    except Exception:
        pass

    original_url: str = page.url
    virtual_pages: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    on_auth_page = bool(AUTH_URL_RE.search(original_url))

    for action in actions:
        if action.get("type") == "type" and on_auth_page:
            print(
                f"[Worker] Skipping typing on auth page {original_url}",
                file=sys.stderr, flush=True,
            )
            continue
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            print("[Worker] Exploration budget spent, stopping", file=sys.stderr, flush=True)
            break

        reacted, new_url, desc = await _execute_action(page, action)
        print(f"[Worker] Action: {desc} -> reacted={reacted}", file=sys.stderr, flush=True)

        if not reacted:
            continue

        # The action produced content (search results, a filtered list, an
        # opened panel). Capture it.
        effective_url: str = new_url or page.url
        if normalize(effective_url) == normalize(original_url):
            # Client-side search: URL unchanged but DOM updated. Capture it
            # under a synthetic path so the results are not lost.
            p_url = urlparse(original_url)
            query = action.get("value", "results")
            effective_url = urlunparse((
                p_url.scheme, p_url.netloc, p_url.path,
                "", f"search={query}", "",
            ))

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        await scroll_page(page)

        page_data = await _capture_page_data(
            page, effective_url, base_url, depth, capture_screenshots
        )
        raw_path = urlparse(effective_url).path.rstrip("/") or "/"
        query_suffix = urlparse(effective_url).query
        # Two searches on the same endpoint share a path; keep them distinct
        # so later pages do not overwrite earlier ones.
        page_data["path"] = f"{raw_path}?{query_suffix}" if query_suffix else raw_path
        if page_data["path"] in seen_paths:
            print(f"[Worker] Skipping duplicate virtual page {page_data['path']}", file=sys.stderr, flush=True)
        else:
            seen_paths.add(page_data["path"])
            virtual_pages.append(page_data)
            print(f"[Worker] Captured virtual page: {page_data['path']}", file=sys.stderr, flush=True)

        # Navigate back for the next action
        try:
            await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[Worker] Failed to navigate back: {e}", file=sys.stderr, flush=True)

    return virtual_pages


def _write_partial(output_file: Optional[str], pages: List[Dict[str, Any]]) -> None:
    """Persist what has been crawled so far.

    The parent kills this process when it overruns its budget, and stdout is
    only written at the very end — without this file a slow site (YouTube)
    loses every page it had already captured.
    """
    if not output_file:
        return
    try:
        tmp = output_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(pages, fh)
        os.replace(tmp, output_file)
    except Exception as e:
        print(f"[Worker] Failed to write partial output: {e}", file=sys.stderr, flush=True)


async def crawl(
    start_url: str,
    max_pages: int,
    max_depth: int,
    timeout: int,
    capture_screenshots: bool = False,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Optional[float] = None,
    output_file: Optional[str] = None,
    headless: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    from playwright.async_api import async_playwright

    if headless is None:
        headless = CRAWLER_HEADLESS

    # Own deadline, a little under the parent's subprocess timeout, so the
    # crawl stops cleanly and returns its pages instead of being killed.
    deadline_ts: Optional[float] = (
        time.monotonic() + budget if budget and budget > 0 else None
    )

    def time_left() -> float:
        return float("inf") if deadline_ts is None else deadline_ts - time.monotonic()

    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = "https://" + start_url
        parsed = urlparse(start_url)
    base_url: str = f"{parsed.scheme}://{parsed.netloc}"
    # Heavy SPAs (YouTube, etc.) need more than the default per-page budget.
    goto_timeout: int = max(timeout, 30) * 1000

    visited: set[str] = set()
    pages: List[Dict[str, Any]] = []
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
        # Headed by default: Cloudflare-style bot checks flag headless Chromium
        # and serve "Just a moment..." instead of the site. Headless is opt-in
        # (CRAWLER_HEADLESS=1) for machines with no display.
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        page.set_default_timeout(8000)
        queue = [(start_url, 0)]

        while queue and len(pages) < max_pages:
            # A fresh page costs a goto, scrolling and clicking; do not start
            # one we cannot finish.
            if time_left() < 20:
                print(
                    f"[Worker] Time budget spent after {len(pages)} pages, stopping early",
                    file=sys.stderr, flush=True,
                )
                break

            url, depth = queue.pop(0)
            norm = normalize(url)
            if norm in visited or depth > max_depth:
                continue
            visited.add(norm)

            print(f"[Worker] -> {url} (depth={depth})", file=sys.stderr, flush=True)

            try:
                # time_left() is infinite when no budget was given, and
                # int(inf) raises - which used to fail every single goto.
                remaining_ms = (
                    goto_timeout
                    if deadline_ts is None
                    else max(int(time_left() * 1000) - 5000, 5000)
                )
                page_goto_timeout = min(goto_timeout, remaining_ms)
                await page.goto(url, wait_until="domcontentloaded", timeout=page_goto_timeout)
            except Exception as e:
                print(f"[Worker] goto failed: {e}", file=sys.stderr, flush=True)
                continue

            try:
                await page.wait_for_selector("body", timeout=5000)
            except Exception:
                pass

            await page.wait_for_timeout(1500)

            blocked = await wait_out_bot_check(page)

            if await dismiss_consent(page):
                await page.wait_for_timeout(2000)

            try:
                txt = await page.inner_text("body")
                if len(txt.strip()) < 100:
                    await page.wait_for_timeout(3000)
            except Exception:
                pass

            await scroll_page(page)

            if depth < 2:
                try:
                    await _neutralize_navigation(page)
                    clicked = await click_selectors(page, explored_selectors, max_clicks=8)
                    if clicked:
                        print(f"[Worker]   clicked {clicked} elements", file=sys.stderr, flush=True)
                        await page.wait_for_timeout(1000)

                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                except Exception:
                    pass

            if normalize(page.url) != normalize(url):
                # Exploration drifted off this URL; reload it so the capture
                # below describes the page we queued, not wherever we landed.
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"[Worker] Failed to reload {url}: {e}", file=sys.stderr, flush=True)

            # Capture the original page
            page_data = await _capture_page_data(page, url, base_url, depth, capture_screenshots)
            page_data["blocked"] = blocked
            pages.append(page_data)
            _write_partial(output_file, pages)
            print(f"[Worker]   {len(pages)}/{max_pages} {page_data['path']} links={len(page_data['links'])}", file=sys.stderr, flush=True)

            # LLM-guided exploration: the model looks at the interactive
            # elements, decides what to click/type (especially search boxes),
            # and we capture any pages it discovers.
            # Exploration costs a model call plus several interactions; skip
            # it rather than have the whole crawl killed mid-page.
            if llm_config and depth < 2 and len(pages) < max_pages and time_left() > 60:
                try:
                    exploration_pages = await _llm_guided_explore(
                        page, llm_config, url, page_data["title"],
                        base_url, depth, capture_screenshots,
                        deadline_ts=(
                            None if deadline_ts is None else deadline_ts - 15
                        ),
                    )
                except Exception as e:
                    print(f"[Worker] LLM exploration failed: {e}", file=sys.stderr, flush=True)
                    exploration_pages = []

                for ep in exploration_pages:
                    if len(pages) >= max_pages:
                        break
                    pages.append(ep)
                    _write_partial(output_file, pages)
                    print(f"[Worker]   +virtual {ep['path']} (LLM exploration)", file=sys.stderr, flush=True)
                    # Queue any new links the exploration revealed
                    for link in ep.get("links", []):
                        if link not in visited:
                            queue.append((link, depth + 1))

                # Make sure we are back on the original page
                if page.url != url:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
                        await page.wait_for_timeout(1000)
                    except Exception:
                        pass

            # Queue links from the original page
            for link in page_data["links"]:
                if link not in visited:
                    queue.append((link, depth + 1))

        await browser.close()
        print(f"[Worker] Done. Total pages: {len(pages)}", file=sys.stderr, flush=True)

    return pages


if __name__ == "__main__":
    data = read_worker_params(sys.argv)
    result = asyncio.run(
        crawl(
            data["url"],
            data["max_pages"],
            data["max_depth"],
            data["timeout"],
            data.get("capture_screenshots", False),
            data.get("llm_config"),
            data.get("budget"),
            data.get("output_file"),
            data.get("headless"),
        )
    )
    print(json.dumps(result))
