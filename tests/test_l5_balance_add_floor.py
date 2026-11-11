"""The balance profile's floor on new exposure is the owner's variable.

OWNER, 2026-11-11: "Support first-leg prices down to $0.05 where venue
liquidity and risk limits permit." Two floors bound a first leg: the
executor's `L5_TS_MIN_FIRST_LEG_PRICE` (already configurable, 0 to 0.50) and
the balance controller's `add_floor`, which was the constant 0.30. This
makes the second configurable too. Default unchanged; a hedge is never
bounded by either.
"""
import pytest

from src import l5_portfolio_controller as PC
from src import l5_two_sided_probe as TS

ENV = PC.BALANCE_ADD_FLOOR_ENV


@pytest.fixture()
def balance(monkeypatch):
    monkeypatch.setenv(PC.PROFILE_ENV, "BALANCE")
    monkeypatch.delenv(ENV, raising=False)
    return monkeypatch


def flat():
    return PC.portfolio(up_qty=0, up_cost=0, dn_qty=0, dn_cost=0)


def quote_sides(bid, ask, t_off=60.0):
    dec = PC.decide(flat(), bid=bid, ask=ask, t_off=t_off,
                    params=PC.with_overrides(PC.production_params(0.01),
                                             base_qty=0.2, max_qty=1e6),
                    limits=PC.Limits(cash_usd=10.0, exposure_usd=10.0,
                                     max_orders=8))
    return ({q["side"] for q in dec["quotes"]},
            {s["side"]: s["reason"] for s in dec["skipped"]})


def test_the_default_is_thirty_cents(balance):
    assert PC.production_params(0.01).add_floor == 0.30


@pytest.mark.parametrize("raw", ["", "abc", "0", "0.04", "0.51", "-0.1",
                                 "nan"])
def test_a_bad_value_cannot_remove_the_floor(balance, raw):
    balance.setenv(ENV, raw)
    assert PC.production_params(0.01).add_floor == 0.30


@pytest.mark.parametrize("raw,want", [("0.05", 0.05), ("0.10", 0.10),
                                      ("0.30", 0.30), ("0.50", 0.50)])
def test_a_value_in_range_is_honoured(balance, raw, want):
    balance.setenv(ENV, raw)
    assert PC.production_params(0.01).add_floor == pytest.approx(want)


def test_it_only_applies_to_the_balance_profile(monkeypatch):
    monkeypatch.setenv(PC.PROFILE_ENV, "")
    monkeypatch.setenv(ENV, "0.05")
    assert PC.production_params(0.01).add_floor == PC.Params().add_floor


def test_at_thirty_a_skewed_book_quotes_only_the_dear_side(balance):
    sides, skipped = quote_sides(0.88, 0.89)
    assert sides == {"UP"}
    assert skipped["DOWN"] == "BELOW_ADD_FLOOR"


def test_at_five_cents_both_sides_of_a_skewed_book_are_quoted(balance):
    balance.setenv(ENV, "0.05")
    sides, skipped = quote_sides(0.88, 0.89)
    assert sides == {"UP", "DOWN"}, skipped


def test_below_five_cents_the_cheap_side_is_still_refused(balance):
    balance.setenv(ENV, "0.05")
    sides, skipped = quote_sides(0.96, 0.97)
    assert "DOWN" not in sides
    assert skipped["DOWN"] == "BELOW_ADD_FLOOR"


def test_a_hedge_is_never_bounded_by_it(balance):
    port = PC.portfolio(up_qty=0.2, up_cost=0.18, dn_qty=0, dn_cost=0)
    dec = PC.decide(port, bid=0.96, ask=0.97, t_off=60.0,
                    params=PC.with_overrides(PC.production_params(0.01),
                                             base_qty=0.2, max_qty=1e6),
                    limits=PC.Limits(cash_usd=10.0, exposure_usd=10.0,
                                     max_orders=8))
    assert [(q["side"], q["role"]) for q in dec["quotes"]] == [
        ("DOWN", "HEDGE")]


def test_the_executor_s_own_floor_already_reaches_five_cents(monkeypatch):
    monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0.05")
    assert str(TS.min_first_leg_own_price()) == "0.05"
