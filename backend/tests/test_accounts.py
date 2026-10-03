"""Accounts, their projects, and the limits that stop the service costing more.

The rules under test are the ones a user feels: one project on the free
tier, one generating run a day, and a repair that costs one action however
many pages it fixes. The last one is the easiest to get wrong, because the
run loops over pages internally.
"""

import threading
from pathlib import Path

import pytest

import accounts


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own database, so no test can spend another's."""
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(accounts, "DB_PATH", path)
    monkeypatch.setattr(accounts, "_initialised", False)
    return path


def an_account(email: str = "one@test.dev") -> accounts.Account:
    return accounts.register(email, "correct horse battery")


def test_a_new_account_can_sign_up_and_back_in() -> None:
    created = an_account()

    found = accounts.authenticate("one@test.dev", "correct horse battery")

    assert found is not None
    assert found.id == created.id
    assert found.email == "one@test.dev"


def test_the_email_does_not_matter_to_who_you_are() -> None:
    an_account("One@Test.DEV")

    assert accounts.authenticate("one@test.dev", "correct horse battery") is not None


def test_the_wrong_password_is_refused() -> None:
    an_account()

    assert accounts.authenticate("one@test.dev", "wrong password") is None


def test_an_unknown_address_is_refused() -> None:
    assert accounts.authenticate("nobody@test.dev", "whatever it is") is None


def test_the_email_that_a_password_is_asked_for_is_not_given_away() -> None:
    """A stranger should not learn who has an account here.

    Answering "no such email" and "wrong password" differently turns the
    sign-in form into a way of checking whether someone is a customer.
    """
    an_account()

    unknown = accounts.authenticate("stranger@test.dev", "correct horse battery")
    wrong = accounts.authenticate("one@test.dev", "correct horse battery ")

    assert unknown is None
    assert wrong is None


def test_the_password_is_not_stored_in_a_readable_form() -> None:
    accounts.register("readable@test.dev", "correct horse battery")

    raw = accounts.DB_PATH.read_bytes()

    assert b"correct horse battery" not in raw


def test_the_same_email_cannot_be_taken_twice() -> None:
    an_account("twice@test.dev")

    with pytest.raises(accounts.AccountError):
        accounts.register("twice@test.dev", "another password")


def test_a_short_password_is_refused() -> None:
    with pytest.raises(accounts.AccountError):
        accounts.register("short@test.dev", "1234567")


def test_something_that_is_not_an_address_is_refused() -> None:
    for bad in ("no-at-sign", "@test.dev", "test.dev@"):
        with pytest.raises(accounts.AccountError):
            accounts.register(bad, "correct horse battery")


def test_a_free_account_keeps_one_project() -> None:
    account = an_account()
    accounts.add_project(account, "run-1", "First")

    with pytest.raises(accounts.NoCapacity):
        accounts.add_project(account, "run-2", "Second")


def test_saving_the_same_run_twice_is_not_a_second_project() -> None:
    account = an_account()
    accounts.add_project(account, "run-1", "First")

    accounts.add_project(account, "run-1", "First")

    assert accounts.usage_of(account).projects == 1


def test_one_account_cannot_see_or_delete_anothers_project() -> None:
    mine = an_account("mine@test.dev")
    theirs = an_account("theirs@test.dev")
    accounts.add_project(theirs, "run-x", "Not yours")

    assert accounts.owns_project(mine, "run-x") is False
    assert accounts.remove_project(mine, "run-x") is False
    assert accounts.owns_project(theirs, "run-x") is True


def test_projects_are_listed_newest_first() -> None:
    account = an_account()
    accounts.set_tier(account.id, "starter")
    accounts.add_project(account, "run-1", "First")
    accounts.add_project(account, "run-2", "Second")

    assert [p["name"] for p in accounts.list_projects(account)] == ["Second", "First"]


def test_a_paid_account_keeps_more_than_one() -> None:
    account = an_account()
    accounts.set_tier(account.id, "studio")

    for index in range(5):
        accounts.add_project(account, f"run-{index}", f"Project {index}")

    assert accounts.usage_of(account).projects == 5


def test_the_free_allowance_is_one_action_a_day() -> None:
    account = an_account()

    first = accounts.spend_action(account)

    assert first.used == 1
    assert first.remaining == 0


def test_over_the_allowance_is_refused() -> None:
    account = an_account()
    accounts.spend_action(account)

    with pytest.raises(accounts.NoCapacity):
        accounts.spend_action(account)


def test_generating_and_editing_have_allowances_of_their_own() -> None:
    """The user counts these as two different things they did."""
    account = an_account()
    accounts.spend_action(account, "generate")

    usage = accounts.spend_action(account, "edit")

    assert usage.remaining == 0
    assert accounts.usage_of(account, "edit").used == 1


def test_a_paid_account_gets_a_bigger_allowance() -> None:
    account = an_account()
    accounts.set_tier(account.id, "starter")
    allowance = accounts._limits(account)["daily_actions"]

    for _ in range(5):
        accounts.spend_action(account)

    # Read off the tier rather than a number typed here: the price of a plan
    # is meant to change, and this test is about a paid account getting more
    # than the free one - not about what "more" happens to be this month.
    assert accounts.usage_of(account).remaining == allowance - 5
    assert allowance > accounts.TIERS["free"]["daily_actions"]


def test_a_refused_action_does_not_leave_a_marks_behind() -> None:
    """Otherwise a day of clicking past the limit still counts as usage."""
    account = an_account()
    accounts.spend_action(account)

    with pytest.raises(accounts.NoCapacity):
        for _ in range(5):
            accounts.spend_action(account)

    assert accounts.usage_of(account).used == 1


def test_tomorrow_starts_over() -> None:
    account = an_account()
    accounts.spend_action(account)

    # The counter is keyed by day, so an older row cannot hold the limit.
    with accounts._connect() as db:
        db.execute("UPDATE usage SET day = '1999-01-01'")

    assert accounts.usage_of(account).remaining == 1


def test_the_allowance_is_shared_by_one_account_only() -> None:
    mine = an_account("mine@test.dev")
    theirs = an_account("theirs@test.dev")
    accounts.spend_action(mine)

    assert accounts.usage_of(theirs).remaining == 1


def test_two_clicks_at_once_cannot_both_pass_the_limit() -> None:
    """The whole reason the counters are rows and not a sum."""
    account = an_account()
    accounts.set_tier(account.id, "free")
    granted: list[int] = []
    refused: list[Exception] = []
    barrier = threading.Barrier(6)

    def _try() -> None:
        barrier.wait()
        try:
            granted.append(accounts.spend_action(account).used)
        except accounts.NoCapacity as exc:
            refused.append(exc)

    threads = [threading.Thread(target=_try) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 1, "only one action was allowed, so one must pass"
    assert len(refused) == 5
    assert accounts.usage_of(account).used == 1


def test_a_run_costs_one_action_however_many_pages_it_fixes() -> None:
    """What the repair does is charged once, before it starts.

    The repair loops over pages and asks the model about each one that did
    not match. Charging per page would bill a single click several times and
    a three page site would be unaffordable on the free tier.
    """
    account = an_account()

    usage = accounts.spend_action(account, "edit")

    assert usage.used == 1


def test_what_is_left_says_when_it_comes_back() -> None:
    account = an_account()

    usage = accounts.usage_of(account)

    assert usage.resets_at > 0
    assert usage.tier == "free"
    assert usage.max_projects == 1


def test_raising_a_tier_opens_the_door_without_touching_the_counter() -> None:
    account = an_account()
    accounts.spend_action(account)
    accounts.set_tier(account.id, "starter")

    # One action spent under the free tier, then the door opened. What is
    # being tested is that the counter was not reset by the change, so the
    # answer is the new allowance less the one already spent.
    usage = accounts.usage_of(account)
    assert usage.remaining == accounts.TIERS["starter"]["daily_actions"] - 1
    assert usage.used == 1


def test_an_unknown_tier_is_refused() -> None:
    account = an_account()

    with pytest.raises(accounts.AccountError):
        accounts.set_tier(account.id, "platinum")