"""Signing in with GitHub or Google, and what they are allowed to reach.

The rule under test throughout: an identity at a provider is its own id, and
an email only ever recognises an account that already exists - and only when
the provider has verified it.
"""

import pytest

import accounts
import db


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(accounts, "_initialised", False)
    yield


class TestProviderAccounts:
    def test_the_same_identity_lands_on_the_same_account(self):
        first = accounts.account_for_provider("github", "1", "a@b.com", True)
        second = accounts.account_for_provider("github", "1", "a@b.com", True)
        assert first.id == second.id

    def test_two_different_people_get_two_accounts(self):
        one = accounts.account_for_provider("github", "1", "a@b.com", True)
        two = accounts.account_for_provider("github", "2", "c@d.com", True)
        assert one.id != two.id

    def test_a_verified_address_opens_the_account_that_already_has_it(self):
        """The point of the whole table: signing in with Google at an address
        someone already signed up with by password should reach that account,
        not leave the person with a second, empty one."""
        existing = accounts.register("a@b.com", "correct horse battery")

        arrived = accounts.account_for_provider("google", "sub-9", "a@b.com", True)

        assert arrived.id == existing.id
        accounts.add_project(existing, "run-1", "Kept")
        assert [p["runId"] for p in accounts.list_projects(arrived)] == ["run-1"]

    def test_an_unverified_address_does_not_open_someone_elses_account(self):
        """The hole this closes. Anyone able to set an unverified address on
        a GitHub account can type anyone's name at it. If that were enough to
        reach an existing account here, their projects would be one click away.
        """
        existing = accounts.register("victim@b.com", "correct horse battery")
        accounts.add_project(existing, "run-private", "Victim's work")

        attacker = accounts.account_for_provider(
            "github", "attacker-1", "victim@b.com", False
        )

        assert attacker.id != existing.id
        assert accounts.list_projects(attacker) == []

    def test_two_providers_are_two_identities(self):
        github = accounts.account_for_provider("github", "1", "a@b.com", True)
        google = accounts.account_for_provider("google", "1", "a@b.com", False)
        assert github.id != google.id

    def test_changing_the_email_keeps_the_same_account(self):
        """A GitHub account whose address changes must not end up pointed at
        whoever holds the new address."""
        first = accounts.account_for_provider("github", "1", "old@b.com", True)

        later = accounts.account_for_provider("github", "1", "new@b.com", True)

        assert later.id == first.id
        assert later.email == "old@b.com"

    def test_both_providers_verifying_one_address_reach_one_account(self):
        """Signing in with either provider at the same address should land in
        the same place - it is the same person, and two buttons on the
        sign-in screen for one person should not mean two accounts."""
        google = accounts.account_for_provider("google", "1", "shared@b.com", True)

        github = accounts.account_for_provider("github", "1", "shared@b.com", True)

        assert github.id == google.id

    def test_only_one_of_them_verifying_it_is_two_people(self):
        """One side checked the address and the other did not, so there is
        nothing tying them to the same person."""
        google = accounts.account_for_provider("google", "1", "shared@b.com", True)

        github = accounts.account_for_provider("github", "1", "shared@b.com", False)

        assert github.id != google.id

    def test_the_account_has_no_password(self):
        """Created out of somebody else's login, so there is no password to
        guess and none to mail a reset to."""
        account = accounts.account_for_provider("github", "1", "a@b.com", True)

        assert accounts.authenticate(account.email, "") is None
        assert accounts.authenticate(account.email, "anything") is None


class TestAProviderThatGivesNoAddress:
    """GitHub with every address private and no user:email scope.

    The address is optional there, so it is genuinely absent rather than
    merely unverified - and it used to reach normalise_email as None, which
    raised AttributeError and turned sign-in into a 500 for those people.
    """

    def test_the_account_is_still_created(self):
        account = accounts.account_for_provider("github", "4242", None, False)

        assert account.id > 0

    def test_the_placeholder_is_clearly_not_a_real_address(self):
        # It must never reach somebody else's inbox, and it must never be
        # mistaken later for something the person actually gave us.
        account = accounts.account_for_provider("github", "4242", None, False)

        assert account.email.endswith("@users.invalid")
        assert "4242" in account.email

    def test_the_same_person_gets_the_same_account(self):
        # Derived from the provider's id, not invented per request - otherwise
        # every sign-in would make another orphan account.
        first = accounts.account_for_provider("github", "4242", None, False)
        second = accounts.account_for_provider("github", "4242", None, False)

        assert first.id == second.id

    def test_two_people_without_addresses_do_not_collide(self):
        first = accounts.account_for_provider("github", "4242", None, False)
        second = accounts.account_for_provider("github", "7777", None, False)

        assert first.id != second.id


class TestProviderTable:
    def test_the_provider_keeps_its_identity_when_the_account_goes(self):
        account = accounts.account_for_provider("github", "1", "a@b.com", True)

        with db.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account.id,))

        # And the identity is free to be claimed again by a new account
        # rather than pointing at a row that no longer exists.
        fresh = accounts.account_for_provider("github", "1", "a@b.com", True)
        assert fresh.id != account.id