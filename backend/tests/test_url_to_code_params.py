import pytest

from routes.url_to_code import (
    DEFAULT_DEPTH,
    DEFAULT_PAGES,
    MAX_DEPTH,
    MAX_PAGES,
    UrlToCodeParams,
    _clamp_int,
    _llm_config_for,
    _normalize_stack,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (5, 5),
        ("7", 7),
        # Out-of-range values are clamped, not silently discarded.
        (999, MAX_PAGES),
        (0, 1),
        (-4, 1),
        # Garbage falls back to the default instead of raising and killing the
        # websocket before the client can be told what went wrong.
        (None, DEFAULT_PAGES),
        ("", DEFAULT_PAGES),
        ("abc", DEFAULT_PAGES),
        ({}, DEFAULT_PAGES),
    ],
)
def test_clamp_int_pages(raw: object, expected: int) -> None:
    assert _clamp_int(raw, DEFAULT_PAGES, 1, MAX_PAGES) == expected


def test_clamp_int_depth_uses_its_own_bounds() -> None:
    assert _clamp_int(50, DEFAULT_DEPTH, 1, MAX_DEPTH) == MAX_DEPTH
    assert _clamp_int(3, DEFAULT_DEPTH, 1, MAX_DEPTH) == 3


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("react_tailwind", "react_tailwind"),
        # The frontend historically sent hyphenated values.
        ("html-tailwind", "html_tailwind"),
        ("bootstrap", "bootstrap"),
        ("nonsense", "html_tailwind"),
        (None, "html_tailwind"),
        ("", "html_tailwind"),
    ],
)
def test_normalize_stack(raw: object, expected: str) -> None:
    assert _normalize_stack(raw) == expected


@pytest.fixture(autouse=True)
def no_keys_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about what the request carries, not about .env.

    They read the configured keys as a fallback, so on a machine with a
    .env of its own "no key was sent" quietly became "the environment
    supplied one" and the assertion below stopped meaning anything.
    """
    import routes.url_to_code as url_to_code

    for name in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setattr(url_to_code, name, None, raising=False)


def test_provider_precedence_matches_documented_order() -> None:
    everything = UrlToCodeParams(
        url="https://example.com",
        custom_provider_base_url="http://localhost:11434/v1",
        custom_provider_model="llama3",
        openrouter_api_key="or-key",
        anthropic_api_key="ant-key",
        openai_api_key="oai-key",
        gemini_api_key="gem-key",
    )
    assert _llm_config_for(everything).provider == "custom"

    without_custom = UrlToCodeParams(
        url="https://example.com",
        openrouter_api_key="or-key",
        anthropic_api_key="ant-key",
    )
    assert _llm_config_for(without_custom).provider == "openrouter"

    anthropic_only = UrlToCodeParams(
        url="https://example.com", anthropic_api_key="ant-key"
    )
    assert _llm_config_for(anthropic_only).provider == "anthropic"

    gemini_only = UrlToCodeParams(url="https://example.com", gemini_api_key="gem-key")
    assert _llm_config_for(gemini_only).provider == "gemini"


def test_no_provider_is_detectable_before_crawling() -> None:
    cfg = _llm_config_for(UrlToCodeParams(url="https://example.com"))

    assert cfg.provider == "none"
    assert not cfg.is_usable
    assert not cfg.api_key
