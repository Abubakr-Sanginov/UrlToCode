import pytest
from typing import Dict, List
from crawler import interaction_states as ist


def node(label: str, selector: str = "button", **extra: object) -> Dict[str, object]:
    return {"selector": selector, "label": label, **extra}


# --- what may be clicked ----------------------------------------------------


def test_an_expanded_control_is_the_strongest_signal():
    # The page itself saying "this opens something" beats any guess from a label.
    ranked = [
        ist.score_control("", "button", "false"),
        ist.score_control("Menu", "button", None),
        ist.score_control("Subscribe", "button", None),
    ]

    assert ranked == sorted(ranked, reverse=True)


def test_an_unlabelled_control_is_never_worth_clicking():
    assert ist.score_control("", "button", None) == 0


@pytest.mark.parametrize("label", ["Copyright 2024", "© 2024 Acme", "All rights reserved"])
def test_a_caption_on_a_button_is_not_treated_as_a_control(label):
    # `<button>© 2024</button>` happens; a visitor never presses it to
    # reveal something.
    assert ist.score_control(label, "button", None) == 0


@pytest.mark.parametrize(
    "label", ["Menu", "Open navigation", "Search", "Войти", "Show cart"]
)
def test_controls_that_say_what_they_open_are_worth_clicking(label):
    assert ist.score_control(label, "button", None) > 0


@pytest.mark.parametrize(
    "label,reason",
    [
        ("Delete account", "sends or changes something"),
        ("Checkout", "sends or changes something"),
        ("Buy now", "sends or changes something"),
        ("Submit", "sends or changes something"),
    ],
)
def test_a_control_that_writes_is_refused_with_a_reason(label, reason):
    allowed, why = ist.safe_to_click(label, None)

    assert allowed is False
    assert reason in why


def test_an_unlabelled_control_is_refused_because_nothing_can_be_told():
    allowed, why = ist.safe_to_click("", None)

    assert allowed is False
    assert "no label" in why


@pytest.mark.parametrize(
    "href",
    [
        "/logout",
        "/checkout",
        "/delete",
        # The dangerous one is rarely at the root: this is what a real
        # "delete my account" link looks like.
        "/account/delete",
        "/settings/billing/cancel",
    ],
)
def test_a_link_that_leaves_or_writes_is_refused(href):
    allowed, why = ist.safe_to_click("Click here", href)

    assert allowed is False
    assert why


def test_a_plain_internal_link_is_allowed():
    # It is still a control worth revealing; whether it navigates is decided
    # afterwards, by checking the page did not change.
    assert ist.safe_to_click("Read more", "/blog/post")[0] is True


def test_a_fragment_link_is_refused_because_it_navigates():
    # Clicking one loses the very state being captured.
    assert ist.safe_to_click("Section", "#top")[0] is False


def test_a_hash_route_link_is_not_refused():
    # `#/pricing` is a router navigation the crawl can follow on its own.
    assert ist.safe_to_click("Pricing", "#/pricing")[0] is True


# --- choosing what to click -------------------------------------------------


def test_the_choosables_are_the_safe_and_promising_ones():
    chosen = ist.choose_controls(
        [
            node("", "button"),
            node("Delete everything", "button"),
            node("Menu", "button"),
            node("Subscribe", "button"),
        ],
        limit=2,
    )

    labels = [entry["label"] for entry in chosen]
    assert "Delete everything" not in labels
    assert "Menu" in labels


def test_the_budget_is_respected():
    nodes: List[Dict[str, object]] = [node(f"Menu {index}") for index in range(20)]

    assert len(ist.choose_controls(nodes, limit=3)) == 3


def test_a_page_with_nothing_clickable_yields_nothing():
    assert ist.choose_controls([node(""), node("Copyright 2024"), node("")]) == []


# --- naming -----------------------------------------------------------------


def test_a_state_is_named_after_what_the_visitor_pressed():
    assert ist.state_name_for(node("Open menu")) == "Open menu"


def test_an_unlabelled_expander_still_gets_a_readable_name():
    assert ist.state_name_for({"label": "", "expanded": "false"}) == "Open"
    assert ist.state_name_for({"label": "", "expanded": "true"}) == "Closed"


def test_a_long_label_is_shortened_rather_than_kept_whole():
    assert len(ist.state_name_for(node("x" * 200))) <= 60


# --- the click script -------------------------------------------------------


def test_the_click_script_targets_one_specific_control():
    script = ist.click_script("button", 2)

    assert "querySelectorAll('button')" in script
    assert "nodes[2]" in script


def test_a_selector_with_a_quote_in_it_still_produces_valid_script():
    # `%r` on the selector, not string interpolation: a page with a quote in
    # its markup must not produce a script that will not parse.
    script = ist.click_script("button[title=\"it's\"]", 0)

    assert "nodes[0]" in script


def test_a_missing_control_reports_failure_rather_than_raising():
    assert "if (!node) return false" in ist.click_script("button", 0)
