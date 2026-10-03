"""What a tier costs and what it actually pays.

These are arithmetic tests about money. They exist because the two numbers
are easy to confuse and the mistake is invisible until a withdrawal comes up
short: charge $5, keep $3.50, and nothing anywhere looks wrong.
"""

import pytest

import accounts
from routes import telegram as telegram_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestTheMarkup:
    def test_the_markup_is_what_the_commission_takes_back(self):
        # 1 / (1 - 0.30) = 1.4286, which is the 43% the price has to carry.
        assert accounts.markup() == pytest.approx(1.4286, abs=0.0001)
        assert (accounts.markup() - 1) == pytest.approx(0.43, abs=0.005)

    def test_a_tier_pays_what_it_says_it_pays(self):
        """The whole point, in one assertion per tier.

        Every paid tier must arrive at or above its advertised price. This
        is the test that would have caught 350 stars being called $5.
        """
        for tier, dollars in accounts.TIER_DOLLARS.items():
            price = accounts.STAR_PRICES[tier]
            assert accounts.stars_net(price) >= dollars, (
                f"{tier} charges {price} stars and pays"
                f" {accounts.stars_net(price)}, which is under ${dollars}"
            )

    def test_pricing_at_the_stated_price_would_be_short(self):
        # The other half: it is not that the prices are generous, it is that
        # charging the plain dollar figure loses money every time. $5 is 250
        # Stars, and 250 Stars pays $3.50.
        stars_for_five = 5 * accounts.STARS_PER_DOLLAR
        assert accounts.stars_net(stars_for_five) == pytest.approx(3.50)

    def test_the_published_markup_matches_the_one_in_use(self):
        # The constant is for reading. If it is ever edited by hand instead
        # of being derived, this says so before a price is quietly wrong.
        assert accounts.STAR_MARKUP == accounts.markup()

    def test_the_price_rounds_up_and_never_down(self):
        # A plan that pays $4.96 when it says $5 is a short the buyer finds
        # out about; one that pays $5.04 looks like rounding.
        assert accounts.stars_net(accounts.stars_for(5)) >= 5
        assert accounts.stars_net(accounts.stars_for(15)) >= 15
        assert accounts.stars_net(accounts.stars_for(45)) >= 45

    def test_the_prices_are_readable_numbers(self):
        # Rounded to a step so the price on a plan does not look like a
        # division that went wrong.
        for price in accounts.STAR_PRICES.values():
            assert price % 180 == 0

    def test_the_free_plan_costs_nothing(self):
        assert "free" not in accounts.STAR_PRICES

    def test_every_paid_plan_has_a_price_and_an_allowance(self):
        for tier, dollars in accounts.TIER_DOLLARS.items():
            assert accounts.STAR_PRICES[tier] > 0
            assert tier in accounts.TIERS
            assert accounts.TIERS[tier]["max_projects"] > 1


class TestTariffEndpoint:
    def test_it_reports_both_numbers_for_every_plan(self):
        app = FastAPI()
        app.include_router(telegram_module.router)

        body = TestClient(app).get("/api/telegram/tariff").json()

        assert len(body["tiers"]) == len(accounts.TIER_DOLLARS)
        for plan in body["tiers"]:
            assert plan["dollars"] == accounts.TIER_DOLLARS[plan["tier"]]
            assert plan["stars"] == accounts.STAR_PRICES[plan["tier"]]
            assert plan["net"] >= plan["dollars"]

    def test_it_says_what_telegram_takes(self):
        app = FastAPI()
        app.include_router(telegram_module.router)

        body = TestClient(app).get("/api/telegram/tariff").json()

        assert body["commission"] == accounts.STAR_COMMISSION


class TestWhenTheRateMoves:
    def test_the_prices_follow_the_rate(self, monkeypatch):
        """If a Star turns out to be worth less than assumed, the price has
        to move with it. Prices typed in by hand do not move on their own,
        which is why they are computed here."""
        monkeypatch.setattr(accounts, "STARS_PER_DOLLAR", 40)

        assert accounts.stars_net(accounts.stars_for(5)) >= 5

    def test_the_prices_follow_the_commission(self, monkeypatch):
        monkeypatch.setattr(accounts, "STAR_COMMISSION", 0.50)

        assert accounts.stars_net(accounts.stars_for(5)) >= 5