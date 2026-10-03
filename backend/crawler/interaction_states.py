"""Capturing the states a page is in besides the one it loads in.

Most of a page is in its initial markup, but some of it only exists after an
action: the mobile menu that slides in, the dialog with its form, the tab
switched over, the accordion opened. A clone built from the default state alone
has a button that does nothing.

The crawl is observational by default and stays that way, because clicking
through a site submits forms, logs in, and can spend money. This module is the
opt-in exception, and it is deliberately narrow: it only clicks things that are
*meant* to be clicked - a button, a summary, a link with a role, an element
with a known state attribute - and it never submits a form, never types into a
field, and never follows a link off the site. Everything it does can be undone
by reloading the page.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Elements that a visitor clicks to reveal something. Deliberately narrow: a
# click on anything else is a guess about what the site does.
_CLICKABLE = (
    "button:not([disabled])",
    "summary",
    "[role='button']:not([aria-disabled='true'])",
    "[aria-expanded]",
    "[aria-controls]",
    "details > summary",
)

# A label that says the element opens something, in the languages a crawled
# site is likely to be in.
_OPENS = re.compile(
    r"\b(menu|hamburger|nav|open|close|toggle|expand|collapse|more|show|hide|"
    r"search|login|sign in|account|cart|filter|view|next|previous|prev|"
    r"меню|открыть|закрыть|развернуть|свернуть|поиск|вход|войти|корзина)\b",
    re.I,
)

# Nothing here may take the page somewhere else or send data: a navigation
# loses the state that was just captured, and a form submission is a write.
# Matched per path segment, not at the start: `/account/delete` is just as
# much of a write as `/delete`, and is far more common.
_FORBIDDEN = re.compile(
    r"(^|/)(logout|signout|sign_out|delete|destroy|checkout|pay|purchase|"
    r"unsubscribe|buy|purchase|order|register|signup|sign_up|remove|"
    r"cancel|deactivate)(/|$)",
    re.I,
)

MAX_STATES_PER_PAGE = 3
MAX_CLICKS_PER_PAGE = 8
# How many candidate controls the page is asked about at all. More than this
# and the probe itself starts to be slow on a page with thousands of buttons.
MAX_NODES = 40

# The selectors the page is asked about, as one comma-separated string so it
# can be handed straight to the browser.
CLICKABLE = ",".join(
    (
        "button:not([disabled])",
        "summary",
        "[role='button']:not([aria-disabled='true'])",
        "[aria-expanded]",
        "[aria-controls]",
        "details > summary",
    )
)

# Text that is a caption, not a control. A copyright line or a stray year on
# a button is not something a visitor presses to reveal something.
_NOT_A_CONTROL = re.compile(
    r"(©|&copy;|\bcopyright\b|\ball rights reserved\b|^\W*\d{4}\W*$)", re.I
)

# The pattern above, as the page sees it: a substring match on a label.
OPEN_PATTERN = _OPENS.pattern

# The script that finds the clickables and says what each one is. It runs in
# the page and returns plain data; the decision to click is made here.
PROBE_JS = """
() => {
  const selectors = %s;
  const wanted = %s;
  const out = [];
  for (const selector of selectors) {
    let nodes;
    try { nodes = document.querySelectorAll(selector); } catch (e) { continue; }
    for (const node of Array.from(nodes)) {
      if (out.length >= %d) break;
      const rect = node.getBoundingClientRect();
      if (rect.width < 8 || rect.height < 8) continue;
      // Hidden on this viewport: clicking it is how a desktop menu opens a
      // phone one, and the click would land on nothing.
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const label = (
        node.getAttribute('aria-label') || node.getAttribute('title') ||
        node.innerText || node.value || ''
      ).trim().slice(0, 80);
      const href = node.closest('a') ? node.closest('a').getAttribute('href') : null;
      out.push({
        selector: selector,
        label: label,
        href: href,
        expanded: node.getAttribute('aria-expanded'),
        controls: node.getAttribute('aria-controls'),
        testId: node.getAttribute('data-testid') || '',
      });
    }
  }
  return { nodes: out, wanted: wanted };
}
"""


def safe_to_click(label: str, href: Optional[str]) -> Tuple[bool, str]:
    """Whether a control may be clicked, and why not when it may not.

    The refusals are the interesting half: a control whose label says it
    submits something is left alone however obvious it looks, because one
    click against a real site is a real action.
    """
    text = (label or "").strip()

    if href:
        path = href.split("?", 1)[0]
        # `search`, not `match`: the segment guard in the pattern is what
        # keeps "delete" from matching "undeleted", and matching at the start
        # of the string would only ever catch paths that begin with it.
        if _FORBIDDEN.search(path):
            return False, "leaves the site or performs an action"
        # A link that only changes the fragment is a router navigation, which
        # loses the state being captured.
        if href.startswith("#") and not href.startswith("#/"):
            return False, "navigates away from the state being captured"

    lowered = text.lower()
    if not text:
        # An unlabelled control is a guess; some of them pay for things.
        return False, "no label, so there is no way to tell what it does"
    if re.search(r"\b(submit|buy|checkout|pay|purchase|delete|remove|send)\b", lowered):
        return False, "sends or changes something on the site"
    return True, ""


def score_control(label: str, selector: str, expanded: Optional[str]) -> int:
    """How likely a control is to reveal a state worth capturing.

    `aria-expanded` is the strongest signal there is: the page itself is
    saying "this opens something". A descriptive label is next, and a bare
    button with no name is barely worth trying.
    """
    if expanded is not None:
        return 3
    if _OPENS.search(label or ""):
        return 2
    if (label or "").strip() and not _NOT_A_CONTROL.search(label or ""):
        return 1
    return 0


def choose_controls(
    nodes: List[Dict[str, object]], limit: int = MAX_STATES_PER_PAGE
) -> List[Dict[str, object]]:
    """The controls worth clicking, best first, within the budget."""
    scored: List[Tuple[int, Dict[str, object]]] = []
    for node in nodes:
        label = str(node.get("label") or "")
        allowed, _ = safe_to_click(label, str(node.get("href")) if node.get("href") else None)
        if not allowed:
            continue
        rank = score_control(
            label,
            str(node.get("selector") or ""),
            str(node["expanded"]) if node.get("expanded") is not None else None,
        )
        if rank <= 0:
            continue
        scored.append((rank, node))

    scored.sort(key=lambda item: -item[0])
    return [node for _, node in scored[:limit]]


def state_name_for(node: Dict[str, object]) -> str:
    """A short, readable name for the state a control reveals."""
    label = str(node.get("label") or "").strip()
    if label:
        return label[:60]
    expanded = node.get("expanded")
    if expanded is not None:
        return "Open" if str(expanded).lower() == "false" else "Closed"
    return "Opened"


def click_script(selector: str, index: int) -> str:
    """JS that clicks one of the page's own controls, by position.

    Clicking by index among the same selector is what a person does - they
    click the button they can see - and it survives a rebuild that changes
    the class names but keeps the order.
    """
    return f"""
(() => {{
  const nodes = document.querySelectorAll({selector!r});
  const node = nodes[{index}];
  if (!node) return false;
  node.scrollIntoView({{block: 'center'}});
  node.click();
  return true;
}})()
"""
