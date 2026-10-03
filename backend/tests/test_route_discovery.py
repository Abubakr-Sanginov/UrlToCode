import pytest
from typing import List
from crawler import route_discovery as rd

BASE = "https://example.com"


# --- hash routes ------------------------------------------------------------


@pytest.mark.parametrize(
    "href,expected",
    [
        ("#/pricing", "/pricing"),
        ("#/products/42", "/products/42"),
        ("#/about/", "/about"),
        ("#/about?ref=nav", "/about"),
        ("#/about#section", "/about"),
    ],
)
def test_a_hash_route_is_a_page(href, expected):
    assert rd.normalise_hash_route(href) == expected


@pytest.mark.parametrize(
    "href",
    [
        "#",
        "#section-two",
        "#pricing",  # a plain anchor, not a route
        "#!",
        "javascript:void(0)",
        "",
    ],
)
def test_an_in_page_anchor_is_not_a_route(href):
    assert rd.normalise_hash_route(href) is None
    assert rd.is_hash_route(href) is False


def test_hash_routes_are_resolved_against_the_page():
    # `#/about` is a route; `#about` is a jump inside this page. Without the
    # leading slash the two are indistinguishable, and guessing wrong would
    # send the crawl after a page that does not exist.
    links = ["#/about", "#pricing", "https://other.com/#/nope", "#anchor"]

    routes = rd.hash_routes_in_links(links, BASE)

    assert routes == ["https://example.com/about"]


# --- XHR-delivered documents ------------------------------------------------


def test_a_background_request_that_returns_html_is_a_page():
    route = rd.route_from_xhr(
        "https://example.com/_next/data/app/page.json", BASE, "text/html"
    )

    assert route is not None
    assert route.startswith("https://example.com/")


def test_a_json_api_call_is_not_a_page():
    # Turning `/api/posts` into a page would send the crawler after endpoints
    # that render nothing on their own.
    assert rd.route_from_xhr("https://example.com/api/posts", BASE, "application/json") is None


def test_a_sitemap_is_a_page_because_it_lists_pages():
    assert rd.route_from_xhr("https://example.com/sitemap.xml", BASE, "application/xml")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/logout",
        "https://example.com/auth/callback",
        "https://example.com/oauth/callback",
        "https://example.com/ws",
    ],
)
def test_endpoints_that_never_render_are_refused(url):
    assert rd.route_from_xhr(url, BASE, "text/html") is None


def test_another_site_is_never_treated_as_our_route():
    assert rd.route_from_xhr("https://other.com/page", BASE, "text/html") is None


def test_a_subdomain_still_counts_as_the_same_site():
    assert rd.same_origin("https://app.example.com/x", BASE) is True
    assert rd.same_origin("https://example.com/x", BASE) is True
    assert rd.same_origin("https://notexample.com/x", BASE) is False


# --- manifests --------------------------------------------------------------


def test_an_xml_sitemap_yields_its_locations():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/</loc></url>
      <url><loc>https://example.com/about</loc></url>
    </urlset>"""

    assert rd.parse_sitemap(xml) == ["https://example.com/", "https://example.com/about"]


def test_a_json_sitemap_yields_its_locations():
    manifest = {"routes": [{"path": "/home"}, {"path": "/docs"}]}

    assert rd.routes_from_manifest(manifest, BASE) == [
        "https://example.com/home",
        "https://example.com/docs",
    ]


def test_a_manifest_full_of_unknown_shapes_yields_nothing_rather_than_guesses():
    assert rd.routes_from_manifest({"weird": 1, "nope": None}, BASE) == []
    assert rd.parse_sitemap("not a document at all") == []


def test_a_broken_sitemap_does_not_raise():
    assert rd.parse_sitemap("<urlset><url><loc>oops") == []


def test_manifest_routes_on_another_site_are_dropped():
    manifest = [{"path": "/home"}, {"path": "https://elsewhere.com/x"}]

    assert rd.routes_from_manifest(manifest, BASE) == ["https://example.com/home"]


# --- merging ----------------------------------------------------------------


def test_already_known_routes_are_not_queued_twice():
    known = {rd.clean("https://example.com/about")}

    added = rd.merge_discovered(known, ["/about", "/pricing", "/pricing#x"], BASE)

    assert added == ["https://example.com/pricing"]


def test_merging_is_bounded_so_a_manifest_cannot_flood_the_queue():
    candidates = [f"/page-{index}" for index in range(500)]

    added = rd.merge_discovered(set(), candidates, BASE, limit=10)

    assert len(added) == 10


def test_merging_refuses_another_site():
    assert rd.merge_discovered(set(), ["https://elsewhere.com/x"], BASE) == []


def test_clean_drops_the_fragment_and_a_trailing_slash():
    assert rd.clean("https://example.com/about/") == "https://example.com/about"
    assert rd.clean("https://example.com/about#top") == "https://example.com/about"
    assert rd.clean("https://example.com/") == "https://example.com/"


# --- router recording -------------------------------------------------------


def test_only_real_urls_from_the_router_are_kept():
    entries: List[object] = ["/products", "", "   ", None, 42]

    assert rd.record_push_state(entries) == ["/products"]  # type: ignore[arg-type]
