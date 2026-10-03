import json
from typing import Any, Dict, List

import clone_mock


def capture(*responses: Dict[str, Any]) -> clone_mock.Capture:
    store = clone_mock.Capture(base_url="https://a.com")
    for entry in responses:
        store.add(
            method=entry.get("method", "GET"),
            url=entry["url"],
            status=entry.get("status", 200),
            content_type=entry.get("content_type", "application/json"),
            raw=entry.get("raw", "{}"),
        )
    return store


# --- what gets kept ---------------------------------------------------------


def test_a_json_answer_is_kept():
    store = capture({"url": "https://a.com/api/products", "raw": '[{"id":1}]'})

    entry = store.entries["GET /api/products"]
    assert entry.body == [{"id": 1}]
    assert entry.status == 200


def test_the_method_is_remembered():
    store = capture(
        {"url": "https://a.com/api/products", "method": "POST", "raw": '{"ok":true}'}
    )

    assert "POST /api/products" in store.entries


def test_a_third_party_request_is_not_the_sites_own_api():
    # An analytics beacon is not the site's data, and its host would end up
    # in every generated file.
    store = capture({"url": "https://cdn.other.com/api/track", "raw": '{"a":1}'})

    assert store.anything is False


def test_a_lookalike_domain_is_still_a_different_site():
    # `nota.com` ends with "ta.com" but is not example.com. A naive suffix
    # check would hand a stranger's responses to the clone.
    store = capture({"url": "https://a.com.evil.net/api/track", "raw": '{"a":1}'})

    assert store.anything is False


def test_the_api_on_a_subdomain_is_the_sites_own():
    # `api.example.com` and `example.com` are one deployment, and the API is
    # very often on the subdomain.
    store = capture({"url": "https://api.a.com/api/products", "raw": "[]"})

    assert store.anything is True


def test_the_port_does_not_make_a_different_site():
    store = capture({"url": "https://a.com:8443/api/products", "raw": "[]"})

    assert store.anything is True


def test_a_path_that_is_not_an_api_is_ignored():
    store = capture({"url": "https://a.com/products", "raw": "[]"})

    assert store.anything is False


def test_a_non_json_answer_is_ignored():
    store = capture(
        {"url": "https://a.com/api/report", "content_type": "text/html", "raw": "<html>"}
    )

    assert store.anything is False


def test_an_error_answer_is_not_the_sites_data():
    # Replaying a 500 would put an error page where the clone expects content.
    store = capture({"url": "https://a.com/api/products", "status": 503, "raw": "{}"})

    assert store.anything is False


def test_something_that_is_not_json_is_ignored():
    store = capture({"url": "https://a.com/api/products", "raw": "not json at all"})

    assert store.anything is False


def test_the_query_string_is_not_part_of_the_key():
    # A cursor or a filter is one visitor's state, not the resource's shape.
    store = capture(
        {"url": "https://a.com/api/products?page=3", "raw": "[]"},
        {"url": "https://a.com/api/products?page=9", "raw": "[]"},
    )

    assert list(store.entries) == ["GET /api/products"]


def test_the_first_answer_wins():
    # A page polls its API; the tenth poll is not a different answer.
    store = capture(
        {"url": "https://a.com/api/products", "raw": '{"n":1}'},
        {"url": "https://a.com/api/products", "raw": '{"n":2}'},
    )

    assert store.entries["GET /api/products"].body == {"n": 1}


def test_an_answer_too_large_to_keep_whole_is_dropped():
    # Cutting a JSON document short leaves something that does not parse, so
    # half of one is not a usable mock: the page renders without it instead.
    store = capture({"url": "https://a.com/api/products", "raw": "[" + '"x",' * 100000 + '"x"]'})

    assert store.anything is False


def test_the_number_of_answers_is_bounded():
    for index in range(clone_mock.MAX_ENTRIES_TOTAL + 40):
        capture({"url": f"https://a.com/api/e{index}", "raw": "{}"})

    assert len(capture({"url": "https://a.com/api/e0", "raw": "{}"}).entries) == 1


def test_one_capture_can_be_merged_into_another():
    first = capture({"url": "https://a.com/api/a", "raw": '{"a":1}'})
    second = capture({"url": "https://a.com/api/b", "raw": '{"b":2}'})

    first.add_page(second)

    assert set(first.entries) == {"GET /api/a", "GET /api/b"}


def test_merging_does_not_overwrite_what_is_already_known():
    first = capture({"url": "https://a.com/api/a", "raw": '{"n":1}'})
    second = capture({"url": "https://a.com/api/a", "raw": '{"n":2}'})

    first.add_page(second)

    assert first.entries["GET /api/a"].body == {"n": 1}


# --- what is removed --------------------------------------------------------


def test_a_credential_is_removed_rather_than_saved():
    store = capture(
        {
            "url": "https://a.com/api/session",
            "raw": '{"user":"ada","access_token":"sk-secret-value"}',
        }
    )

    body = store.entries["GET /api/session"].body
    assert body["user"] == "ada"
    assert body["access_token"] == "[removed]"
    assert "sk-secret-value" not in json.dumps(body)


def test_a_credential_is_found_at_any_depth():
    store = capture(
        {
            "url": "https://a.com/api/account",
            "raw": json.dumps({"profile": {"settings": {"apiKey": "live_x"}}}),
        }
    )

    assert "live_x" not in json.dumps(store.entries["GET /api/account"].body)


def test_a_credential_inside_a_list_is_found():
    store = capture(
        {
            "url": "https://a.com/api/tokens",
            "raw": json.dumps([{"name": "a", "password": "hunter2"}]),
        }
    )

    assert "hunter2" not in json.dumps(store.to_json())


def test_a_payload_nested_past_anything_real_is_dropped():
    # A circular or absurdly deep structure must not be walked forever.
    deep: Any = "leaf"
    for _ in range(clone_mock.MAX_DEPTH + 5):
        deep = {"next": deep}

    assert clone_mock._scrub(deep, 0) is not None


# --- round trip -------------------------------------------------------------


def test_a_capture_survives_being_stored_and_read_back():
    store = capture({"url": "https://a.com/api/products", "raw": '[{"id":1,"name":"Cup"}]'})

    restored = clone_mock.Capture.from_json(store.to_json())

    assert restored.entries["GET /api/products"].body == [{"id": 1, "name": "Cup"}]


def test_a_stored_capture_still_builds_its_mock_layer():
    store = capture({"url": "https://a.com/api/products", "raw": "[]"})
    restored = clone_mock.Capture.from_json(store.to_json())

    assert clone_mock.build_mock_layer(restored)


def test_rubbish_in_a_stored_capture_is_ignored_rather_than_half_read():
    restored = clone_mock.Capture.from_json(
        {"GET /api/a": "not an object", "GET /api/b": {"status": 200, "body": []}}
    )

    assert list(restored.entries) == ["GET /api/b"]


# --- the shipped layer ------------------------------------------------------


def test_nothing_captured_means_no_mock_layer():
    # A layer with no entries would intercept real requests and answer them
    # with nothing, which is worse than not having one.
    assert clone_mock.build_mock_layer(clone_mock.Capture()) == {}


def test_the_layer_ships_the_answers_and_a_way_to_use_them():
    files = clone_mock.build_mock_layer(capture({"url": "https://a.com/api/x", "raw": "[]"}))

    assert set(files) == {"mock/api.json", "mock/mock.js", "mock/README.md"}
    assert "GET /api/x" in json.loads(files["mock/api.json"])
    assert "window.fetch" in files["mock/mock.js"]
    assert "mock.js" in files["mock/README.md"]


def test_a_request_that_was_not_captured_passes_through():
    # The pages can be left wired to the real API while the mock fills in the
    # endpoints it has, one at a time.
    script = clone_mock.build_mock_layer(capture({"url": "https://a.com/api/x", "raw": "[]"}))[
        "mock/mock.js"
    ]

    assert "realFetch(input, init)" in script


def test_the_readme_says_what_was_removed():
    files = clone_mock.build_mock_layer(
        capture({"url": "https://a.com/api/s", "raw": '{"token":"abc","n":1}'})
    )

    assert "[removed]" in files["mock/README.md"]


def test_the_summary_lists_what_was_captured():
    store = capture({"url": "https://a.com/api/products", "raw": "[]"})

    assert "GET /api/products" in store.summary()


def test_the_summary_of_nothing_is_empty():
    assert clone_mock.Capture().summary() == ""