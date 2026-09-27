import pytest

from clone_site import (
    INDEX_FILENAME,
    page_path_to_filename,
    resolve_provider,
)


def test_root_path_maps_to_index():
    assert page_path_to_filename("/", 0) == INDEX_FILENAME
    assert page_path_to_filename("", 0) == INDEX_FILENAME


def test_simple_path_gets_html_extension():
    assert page_path_to_filename("/about", 0) == "about.html"
    assert page_path_to_filename("/about/", 0) == "about.html"


def test_nested_path_keeps_directory_structure():
    assert page_path_to_filename("/blog/post-1", 0) == "blog/post-1.html"


def test_existing_html_extension_is_kept():
    assert page_path_to_filename("/page.html", 0) == "page.html"


def test_traversal_path_falls_back_to_safe_name():
    assert page_path_to_filename("/../../etc/passwd", 3) == "page_3.html"


def test_trailing_slash_root_variants():
    assert page_path_to_filename("/products/list/", 1) == "products/list.html"


class _Args:
    url = "https://example.com"
    provider = None
    base_url = None
    api_key = None
    model = None


def test_resolve_provider_requires_base_url_for_custom(monkeypatch):
    args = _Args()
    args.provider = "custom"
    with pytest.raises(SystemExit):
        resolve_provider(args)


def test_resolve_provider_mock_needs_nothing():
    args = _Args()
    args.provider = "mock"
    provider, api_key, base_url = resolve_provider(args)
    assert provider == "mock"
    assert api_key is None
    assert base_url is None


def test_resolve_provider_base_url_implies_custom():
    args = _Args()
    args.base_url = "http://127.0.0.1:8000/v1"
    args.api_key = None
    provider, api_key, base_url = resolve_provider(args)
    assert provider == "custom"
    assert api_key == "no-key"
    assert base_url == "http://127.0.0.1:8000/v1"


def test_resolve_provider_named_provider_without_key_exits(monkeypatch):
    import clone_site

    monkeypatch.setattr(clone_site, "ANTHROPIC_API_KEY", None)
    args = _Args()
    args.provider = "anthropic"
    with pytest.raises(SystemExit):
        resolve_provider(args)
