from prompts.update.attached_page import (
    AttachedPage,
    build_attached_pages_block,
    find_page_urls,
)


def _block_of(pages: list[AttachedPage]) -> str:
    return build_attached_pages_block(pages)


def test_find_page_urls_matches_full_urls_and_bare_domains() -> None:
    urls = find_page_urls(
        "Study https://domain.com/login and also add domain.com/pricing please"
    )
    assert urls == [
        "https://domain.com/login",
        "https://domain.com/pricing",
    ]


def test_find_page_urls_ignores_sentences_and_versions() -> None:
    text = "This v1.2 costs e.g. 5 dollars, see fig. 3. Wait... ok"
    assert find_page_urls(text) == []


def test_find_page_urls_strips_trailing_punctuation() -> None:
    assert find_page_urls("Clone domain.com/login.") == ["https://domain.com/login"]


def test_find_page_urls_is_capped_and_deduplicated() -> None:
    text = " ".join(
        f"https://site{i}.com/page" for i in range(5)
    ) + " https://site0.com/page again"
    urls = find_page_urls(text)
    assert len(urls) == 2


def test_block_instructs_to_wire_the_matching_control() -> None:
    page = AttachedPage(
        url="https://domain.com/login",
        path="/login",
        title="Sign in",
        filename="login.html",
        structure='<html><body><h1>Sign in</h1></body></html>',
    )

    block = _block_of([page])

    assert page.url in block
    assert "login.html" in block
    assert 'path="/login"' in block
    assert "1:1" in block
    assert "Sign in" in block


def test_block_is_empty_without_pages() -> None:
    assert build_attached_pages_block([]) == ""
