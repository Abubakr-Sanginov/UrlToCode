"""Compact raw crawled HTML into a token-efficient structural skeleton.

The crawler captures full page markup, which is dominated by content that is
useless for design recreation: script/style bodies, tracking attributes,
framework-generated class soup, and long runs of structurally identical
siblings (feed rows, product cards, nav lists).

`compact_html` keeps the parts a model needs to rebuild a layout -- tag
structure, visible text, links, images, form fields -- and drops or collapses
the rest. On real pages this cuts prompt size by 10-150x versus a raw slice.
"""

from __future__ import annotations

import re
import warnings
from typing import Any, Callable, Iterable, Sequence, cast

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

try:  # bs4 >= 4.11
    from bs4 import MarkupResemblesLocatorWarning as _LocatorWarning

    _MARKUP_WARNING: type[Warning] | None = _LocatorWarning
except ImportError:  # pragma: no cover - older bs4
    _MARKUP_WARNING = None

# Tags whose contents never inform visual recreation.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "canvas",
    "link",
    "meta",
    "base",
)

# Tags that are meaningful even when they hold no text.
_VOID_TAGS = frozenset({"img", "input", "br", "hr", "source", "video"})
# Descendants that make an otherwise-empty wrapper worth keeping.
_INTERACTIVE_TAGS = ["img", "input", "a", "button", "select", "textarea", "video"]

# Attributes worth keeping, per tag. Everything else is dropped.
# data-bg-image is set by the crawler to the element's CSS background image.
_GLOBAL_KEEP_ATTRS: tuple[str, ...] = ("class", "role", "type", "data-bg-image")
_TAG_KEEP_ATTRS: dict[str, tuple[str, ...]] = {
    "a": ("href",),
    "img": ("src", "alt"),
    "video": ("src", "poster", "autoplay", "loop", "muted", "playsinline", "controls"),
    "source": ("src", "type"),
    "form": ("action", "method"),
    "input": ("name", "placeholder", "value"),
    "textarea": ("name", "placeholder"),
    "select": ("name",),
    "option": ("value",),
    "button": ("name",),
    "label": ("for",),
}
_URL_ATTRS = frozenset({"href", "src", "action", "poster", "data-bg-image"})

# Number of structurally identical siblings to keep before collapsing.
_REPEAT_KEEP = 3
# Siblings must exceed this count before collapsing kicks in.
_REPEAT_THRESHOLD = 5

_MAX_CLASSES = 4
_MAX_CLASS_LEN = 40
_MAX_URL_LEN = 80
# Image sources are copied into the clone verbatim, so they get more room: a
# URL cut off with "..." is a broken image rather than a shorter one.
_MAX_SRC_LEN = 240
_MAX_TEXT_LEN = 120

_WS_RE = re.compile(r"\s+")
# Utility-framework classes carry layout meaning; hashed/generated ones do not.
_GENERATED_CLASS_RE = re.compile(
    r"""^(?:
        [a-z]+-[0-9a-f]{6,}      # styled-components / emotion style hashes
        | css-[0-9a-z]{5,}       # emotion
        | jsx-\d+                # next.js
        | sc-[0-9a-zA-Z]{6,}     # styled-components
        | ng-[a-z0-9-]+          # angular internals
        | [0-9a-f]{8,}           # bare hashes
    )$""",
    re.VERBOSE,
)

# bs4 is untyped in places; these helpers keep the narrowing in one spot.
_Node = Tag | BeautifulSoup


def _find_all(node: _Node, *args: Any, **kwargs: Any) -> list[Any]:
    """`find_all` with its result pinned to a concrete list type."""
    return list(cast("Iterable[Any]", node.find_all(*args, **kwargs)))


def _child_tags(node: _Node) -> list[Tag]:
    return [
        child for child in _find_all(node, recursive=False) if isinstance(child, Tag)
    ]


def _all_tags(node: _Node, names: Sequence[str] | bool = True) -> list[Tag]:
    return [tag for tag in _find_all(node, names) if isinstance(tag, Tag)]


def _all_strings(node: _Node) -> list[NavigableString]:
    return [
        s for s in _find_all(node, string=True) if isinstance(s, NavigableString)
    ]


def _attrs_of(tag: Tag) -> dict[str, Any]:
    return cast(dict[str, Any], tag.attrs)


def _clean_text(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


def _keep_attrs_for(tag_name: str) -> tuple[str, ...]:
    return _GLOBAL_KEEP_ATTRS + _TAG_KEEP_ATTRS.get(tag_name, ())


def _as_class_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in cast("Iterable[object]", raw)]
    return []


def _filter_classes(raw: object) -> list[str]:
    kept: list[str] = []
    for cls in _as_class_list(raw):
        if not cls or len(cls) > _MAX_CLASS_LEN:
            continue
        if _GENERATED_CLASS_RE.match(cls):
            continue
        kept.append(cls)
        if len(kept) >= _MAX_CLASSES:
            break
    return kept


# Rewrites a URL attribute before it is shortened: (attribute, raw value) ->
# value. Used to make relative URLs absolute and point crawled pages at their
# local files, since a relative src/href is dead once the clone is saved.
UrlMapper = Callable[[str, str], str]


def _shorten_url(value: str, limit: int = _MAX_URL_LEN) -> str:
    value = value.strip()
    if value.startswith("data:"):
        return "data:..."
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _strip_attributes(tag: Tag, url_mapper: UrlMapper | None = None) -> None:
    attrs = _attrs_of(tag)
    keep = _keep_attrs_for(tag.name)

    for name in list(attrs.keys()):
        if name not in keep:
            del attrs[name]
            continue

        value: object = attrs[name]
        if name == "class":
            classes = _filter_classes(value)
            if classes:
                attrs[name] = classes
            else:
                del attrs[name]
        elif name in _URL_ATTRS:
            raw = str(value)
            if url_mapper is not None:
                raw = url_mapper(name, raw)
            limit = _MAX_URL_LEN if name in ("href", "action") else _MAX_SRC_LEN
            attrs[name] = _shorten_url(raw, limit)
        elif isinstance(value, str) and len(value) > _MAX_TEXT_LEN:
            attrs[name] = value[:_MAX_TEXT_LEN] + "..."


def _shape_signature(tag: Tag) -> str:
    """Structural fingerprint used to detect repeated siblings."""
    classes = _as_class_list(_attrs_of(tag).get("class"))
    child_names: list[str] = [child.name for child in _child_tags(tag)[:6]]
    return f"{tag.name}|{'.'.join(sorted(classes))}|{','.join(child_names)}"


def _collapse_repeats(parent: _Node) -> None:
    """Keep the first few of each repeated sibling group, note the remainder."""
    children = _child_tags(parent)
    if len(children) <= _REPEAT_THRESHOLD:
        return

    seen: dict[str, int] = {}
    dropped: dict[str, int] = {}
    last_kept: dict[str, Tag] = {}

    for child in children:
        signature = _shape_signature(child)
        count = seen.get(signature, 0) + 1
        seen[signature] = count
        if count <= _REPEAT_KEEP:
            last_kept[signature] = child
        else:
            dropped[signature] = dropped.get(signature, 0) + 1
            child.decompose()

    for signature, count in dropped.items():
        anchor = last_kept.get(signature)
        if anchor is not None:
            anchor.insert_after(Comment(f" +{count} similar items "))


def _prune_empty(tag: _Node) -> None:
    """Remove wrappers that carry neither text nor meaningful descendants."""
    for node in _child_tags(tag):
        _prune_empty(node)

    for node in _child_tags(tag):
        if node.name in _VOID_TAGS:
            continue
        if node.find(_INTERACTIVE_TAGS) is not None:
            continue
        # A background image is content even in an element without text.
        if node.has_attr("data-bg-image") or node.find(attrs={"data-bg-image": True}):
            continue
        if node.get_text(strip=True):
            continue
        node.decompose()


def _normalize_text(node: _Node) -> None:
    for string in _all_strings(node):
        original = str(string)
        cleaned = _clean_text(original)
        if not cleaned:
            string.extract()
        elif len(cleaned) > _MAX_TEXT_LEN:
            string.replace_with(cleaned[:_MAX_TEXT_LEN] + "...")
        elif cleaned != original:
            string.replace_with(cleaned)


def compact_html(
    html: str, max_chars: int = 6000, url_mapper: UrlMapper | None = None
) -> str:
    """Return a structure-preserving, token-efficient version of `html`.

    Drops non-visual tags and attributes, collapses runs of structurally
    identical siblings, and truncates to `max_chars`. Falls back to a plain
    slice if the markup cannot be parsed. `url_mapper`, when given, rewrites
    every href/src/action before it is shortened.
    """
    if not html.strip():
        return ""

    try:
        with warnings.catch_warnings():
            if _MARKUP_WARNING is not None:
                warnings.simplefilter("ignore", _MARKUP_WARNING)
            soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return html[:max_chars]

    for comment in _all_strings(soup):
        if isinstance(comment, Comment):
            comment.extract()

    for tag in _all_tags(soup, _DROP_TAGS):
        tag.decompose()

    body: _Node = soup.body if isinstance(soup.body, Tag) else soup

    for tag in _all_tags(body):
        _strip_attributes(tag, url_mapper)

    _normalize_text(body)
    _prune_empty(body)

    for tag in _all_tags(body):
        _collapse_repeats(tag)
    _collapse_repeats(body)

    compacted = _WS_RE.sub(" ", body.decode()).strip()
    if len(compacted) <= max_chars:
        return compacted
    return compacted[:max_chars] + "\n<!-- truncated -->"
