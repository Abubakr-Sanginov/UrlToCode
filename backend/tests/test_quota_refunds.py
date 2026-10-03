"""A failed run must not cost the user a day's allowance.

The unit is taken before the work starts, because a limit checked after
the model has been paid for is not a limit. These tests pin the other
half of that bargain: when nothing usable comes back, the unit is handed
back.
"""

import pytest

import accounts as accounts_module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts_module, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts_module, "_initialised", False)
    yield


def an_account(email="a@b.dev"):
    return accounts_module.register(email, "correct horse battery")


def test_a_failed_run_gives_the_unit_back(isolated):
    account = an_account()
    accounts_module.spend_action(account, "generate")

    accounts_module.refund_action(account, "generate")

    assert accounts_module.usage_of(account, "generate").remaining == 1


def test_a_free_account_that_failed_may_still_try_again(isolated):
    # The complaint this fixes: "it failed, and it still said 1 left" only
    # ever happens the other way round, so check the direction that hurts.
    account = an_account()
    accounts_module.spend_action(account, "generate")
    accounts_module.refund_action(account, "generate")

    # A second attempt must be allowed, and must be allowed to be spent.
    assert accounts_module.spend_action(account, "generate").used == 1


def test_a_refund_cannot_invent_allowance(isolated):
    account = an_account()
    accounts_module.spend_action(account, "generate")

    accounts_module.refund_action(account, "generate")
    accounts_module.refund_action(account, "generate")
    accounts_module.refund_action(account, "generate")

    # Never below zero: a second refund has nothing to return.
    assert accounts_module.usage_of(account, "generate").used == 0
    assert accounts_module.usage_of(account, "generate").remaining == 1


def test_a_refund_does_not_give_back_someone_elses_unit(isolated):
    mine = an_account("mine@b.dev")
    theirs = an_account("theirs@b.dev")
    accounts_module.spend_action(mine, "generate")

    accounts_module.refund_action(theirs, "generate")

    assert accounts_module.usage_of(mine, "generate").used == 1
    assert accounts_module.usage_of(theirs, "generate").used == 0


def test_generating_and_editing_are_refunded_separately(isolated):
    account = an_account()
    accounts_module.spend_action(account, "generate")

    accounts_module.refund_action(account, "edit")

    # The wrong counter must not be touched.
    assert accounts_module.usage_of(account, "generate").used == 1


def test_a_refund_from_a_day_with_no_usage_is_not_an_error(isolated):
    account = an_account()

    usage = accounts_module.refund_action(account, "generate")

    assert usage.remaining == 1


def test_an_unknown_action_is_still_refused(isolated):
    account = an_account()
    with pytest.raises(accounts_module.AccountError):
        accounts_module.refund_action(account, "render")
