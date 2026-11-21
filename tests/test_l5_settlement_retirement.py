"""Settlement is what lets the executor run more than one window.

`committed` was incremented on every fill and retired by nothing, so
`open_markets` named the first market forever and MAX_CONCURRENT_MARKETS
refused every window after it. Every probe before this ran one window and
exited, so nothing ever noticed.

Settlement is also the only event that moves `realized_today`, which
SafetyLimits.refuse reads for the daily loss halt and which, until now, was
written by nothing at all -- the halt was wired and dead.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_transport as T


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def hold(store, market, up, dn, up_cost, dn_cost, committed):
    store.state["positions"]["%s|UP" % market] = str(up)
    store.state["positions"]["%s|DOWN" % market] = str(dn)
    store.state.setdefault("leg_cost", {})
    store.state["leg_cost"]["%s|UP" % market] = str(up_cost)
    store.state["leg_cost"]["%s|DOWN" % market] = str(dn_cost)
    store.state["committed"][market] = str(committed)


# ------------------------------------------------- the settlement price is 0

def test_a_settlement_of_zero_is_a_real_answer_not_a_missing_one():
    """`_as_decimal` returns None for anything <= 0 because a tick size of 0
    is meaningless. A settlement price of 0 is not a constraint -- it is what
    this venue reports when the long side LOST. Reusing the constraint parser
    would refuse to settle about half of all markets."""
    assert T.settlement_decimal({"value": "0.0000"}) == D("0.0000")
    assert T.settlement_decimal("0") == D(0)
    assert T._as_decimal("0") is None            # the trap, pinned
    assert T.settlement_decimal({"value": "1.0000"}) == D("1.0000")


@pytest.mark.parametrize("bad", [None, "x", "1.5", "-0.1", {}, {"value": None}])
def test_a_settlement_price_outside_zero_to_one_is_refused(bad):
    assert T.settlement_decimal(bad) is None


def test_closed_is_not_terminal_because_the_price_is_not_final_yet():
    """MARKET_STATE_CLOSED appears at the 900s boundary before settlement.
    Retiring against a price that is not final closes the book at the wrong
    number."""
    assert "MARKET_STATE_CLOSED" not in T.TERMINAL_MARKET_STATES
    assert "MARKET_STATE_EXPIRED" in T.TERMINAL_MARKET_STATES


# --------------------------------------------------------- the arithmetic

def test_up_won_so_down_shares_redeem_nothing(store):
    hold(store, "m", D("4.72"), D("3.93"), D("3.319898"), D("2.0"), D("7.32"))
    out = store.retire_settled_market("m", yes_value=D(1))
    assert out["retired"] is True
    assert D(out["redemption_total"]) == D("4.72")      # == up_qty
    assert D(out["matched_redeem"]) == D("3.93")        # a pair pays $1
    assert out["residual_side"] == "UP"
    assert D(out["residual_qty"]) == D("0.79")


def test_down_won_so_the_short_leg_is_what_pays(store):
    hold(store, "m", D("4.72"), D("3.93"), D("3.319898"), D("2.0"), D("7.32"))
    out = store.retire_settled_market("m", yes_value=D(0))
    assert D(out["redemption_total"]) == D("3.93")      # == dn_qty
    assert D(out["matched_redeem"]) == D("3.93")


def test_a_matched_book_redeems_the_same_whichever_way_it_settles(store):
    """The point of the strategy: matched inventory is locked at fill time."""
    got = []
    for v in (D(0), D(1)):
        with tempfile.TemporaryDirectory() as d:
            s = LX.InventoryStore(os.path.join(d, "i.json"))
            hold(s, "m", D("2"), D("2"), D("1.10"), D("0.95"), D("2.05"))
            got.append(D(s.retire_settled_market("m", yes_value=v)["settlement_pnl"]))
    assert got[0] == got[1]
    assert got[0] == D("2") - D("2.05")


def test_matched_and_residual_are_returned_apart_and_not_summed(store):
    hold(store, "m", D("4.72"), D("3.93"), D("3.319898"), D("2.0"), D("7.32"))
    out = store.retire_settled_market("m", yes_value=D(1))
    assert "matched_pnl" in out and "residual_pnl" in out
    assert (D(out["matched_pnl"]) + D(out["residual_pnl"])
            == D(out["settlement_pnl"]))
    assert "NOT summed" in out["labels"]


# --------------------------------------------------- what it actually frees

def test_retiring_frees_the_concurrency_slot(store):
    """The whole reason this exists."""
    hold(store, "m1", D("1"), D("1"), D("0.5"), D("0.5"), D("1.0"))
    assert [m for m, v in store.committed().items() if v > 0] == ["m1"]
    store.retire_settled_market("m1", yes_value=D(1))
    assert [m for m, v in store.committed().items() if v > 0] == []
    assert store.markets_with_exposure() == []


def test_a_market_blocks_a_second_one_until_it_is_retired():
    """MAX_CONCURRENT_MARKETS, end to end."""
    lim = LX.SafetyLimits(max_concurrent_markets=1, max_live_orders=8,
                          max_usd_per_market=D(5),
                          max_total_exposure_usd=D(5))
    kw = dict(price=D("0.5"), qty=D("1"), residual_after=D(0),
              realized_today=D(0), outstanding_by_market={},
              outstanding_residual=D(0), live_order_count=0,
              orders_uncertain=False, risk_by_market={})
    assert lim.refuse(market="m2", committed_by_market={"m1": D(1)},
                      open_markets=["m1"], **kw) == "MAX_CONCURRENT_MARKETS"
    assert lim.refuse(market="m2", committed_by_market={},
                      open_markets=[], **kw) != "MAX_CONCURRENT_MARKETS"


def test_settlement_is_the_only_thing_that_moves_realized_today(store):
    """The daily loss halt reads realized_today and nothing wrote it."""
    assert store.realized_today() == D(0)
    hold(store, "m", D("1"), D(0), D("0.90"), D(0), D("0.90"))
    store.retire_settled_market("m", yes_value=D(0))     # UP lost: -0.90
    assert store.realized_today() == D("-0.90")


def test_the_daily_halt_can_now_actually_fire(store):
    lim = LX.SafetyLimits(daily_realized_loss_halt_usd=D(5))
    kw = dict(price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
              residual_after=D(0), open_markets=[], outstanding_by_market={},
              outstanding_residual=D(0), live_order_count=0,
              orders_uncertain=False, risk_by_market={})
    assert lim.refuse(realized_today=D("-4.99"), **kw) != "DAILY_REALIZED_LOSS_HALT"
    assert lim.refuse(realized_today=D("-5.00"), **kw) == "DAILY_REALIZED_LOSS_HALT"


# ------------------------------------------------------------- the refusals

def test_a_market_with_a_live_order_is_not_retired(store):
    """An order that can still fill is exposure; releasing the cap against it
    would let the next market's first leg sit on top of it."""
    hold(store, "m", D("1"), D("1"), D("0.5"), D("0.5"), D("1.0"))
    store.state.setdefault("live_orders", {})["coid"] = {"market": "m"}
    out = store.retire_settled_market("m", yes_value=D(1))
    assert out["retired"] is False
    assert out["reason"] == "LIVE_ORDERS_IN_MARKET"
    assert store.committed().get("m") == D("1.0")        # nothing released


@pytest.mark.parametrize("v", [D("-1"), D("2")])
def test_a_settlement_outside_zero_to_one_retires_nothing(store, v):
    hold(store, "m", D("1"), D("1"), D("0.5"), D("0.5"), D("1.0"))
    out = store.retire_settled_market("m", yes_value=v)
    assert out["retired"] is False
    assert store.committed().get("m") == D("1.0")


def test_exposure_from_a_reconciled_restart_is_still_swept(store):
    """reconcile_from_venue writes positions without touching committed, so a
    market recovered after a crash has one and not the other."""
    store.state["positions"]["m|UP"] = "1"
    assert store.committed().get("m") is None
    assert "m" in store.markets_with_exposure()
