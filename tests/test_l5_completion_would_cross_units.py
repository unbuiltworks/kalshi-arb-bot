"""The maker-only assertion must read the OWN price, not the wire price.

`complement_order` returns `leg["price"]` in OWN terms with `wire_price`
beside it, and the two sum to 1.00. `_would_cross` takes the OWN price and
does the wire conversion itself. Passing `1 - price` handed it the wire
price, so for DOWN it converted twice and tested the wrong inequality --
refusing every DOWN completion that rested at the touch.

UP was unaffected (the conversion is the identity there), which is exactly
why the defect survived: the windows that completed were UP completions.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_two_sided_probe as TSP


# ------------------------------------------------- the unit contract itself

def test_complement_order_returns_the_own_price_with_wire_beside_it():
    r = TSP.complement_order(
        side="DOWN", qty=D(2), cost_px=D("0.46"), bid=D("0.90"),
        ask=D("0.91"), tick=D("0.01"), min_qty=D("0.01"), depth_ticks=0,
        held_qty=D(2), held_cost_usd=D("0.92"),
        residual_budget_usd=D("1.50"), book_age_s=0.1)
    assert r["ok"] is True
    leg = r["leg"]
    assert D(leg["price"]) == D("0.09")          # own
    assert D(leg["wire_price"]) == D("0.91")     # wire
    assert D(leg["price"]) + D(leg["wire_price"]) == D(1)
    assert leg["resting_side"] == "ASK"


@pytest.mark.parametrize("bid,ask", [("0.90", "0.91"), ("0.70", "0.71"),
                                     ("0.55", "0.57"), ("0.33", "0.34")])
def test_a_DOWN_quote_at_the_touch_does_not_cross(bid, ask):
    """A DOWN complement at the touch is a YES offer AT the ask: a maker."""
    own = TSP.touch_own_price("DOWN", bid=D(bid), ask=D(ask))
    assert BC._would_cross("DOWN", own, D(bid), D(ask)) is False


@pytest.mark.parametrize("bid,ask", [("0.90", "0.91"), ("0.70", "0.71")])
def test_the_wire_price_passed_as_own_is_what_produced_the_false_positive(
        bid, ask):
    """Pin the defect itself, so a reintroduction fails here."""
    own = TSP.touch_own_price("DOWN", bid=D(bid), ask=D(ask))
    wire = D(1) - own
    assert BC._would_cross("DOWN", own, D(bid), D(ask)) is False
    assert BC._would_cross("DOWN", wire, D(bid), D(ask)) is True


def test_a_DOWN_quote_that_really_would_cross_is_still_refused():
    """The fix must not disarm the assertion it corrects."""
    # a YES offer BELOW the bid is a cross: own price above 1 - bid
    assert BC._would_cross("DOWN", D("0.20"), D("0.90"), D("0.91")) is True
    # and at the bid exactly
    assert BC._would_cross("DOWN", D("0.10"), D("0.90"), D("0.91")) is True


def test_UP_is_unchanged_in_both_directions():
    assert BC._would_cross("UP", D("0.90"), D("0.90"), D("0.91")) is False
    assert BC._would_cross("UP", D("0.91"), D("0.90"), D("0.91")) is True
    assert BC._would_cross("UP", D("0.95"), D("0.90"), D("0.91")) is True


# ------------------------------------- the Stage A 2130z state, end to end

def _decide(bid, ask, held_side, held_qty, held_cost, **kw):
    base = dict(
        market="cpc-btc-updown-15m-2026-10-30-2130z",
        held_side=held_side, held_qty=D(held_qty),
        held_cost_usd=D(held_cost),
        book={"bid": D(bid), "ask": D(ask), "stream_age_s": 0.1,
              "bid_shares": D("500"), "ask_shares": D("500")},
        now_t=1000.0, window_start=400.0,
        tick="0.01", min_qty="0.01", depth_ticks=0,
        residual_budget_usd=D("1.50"))
    base.update(kw)
    return BC.decide(**base)


def test_the_2130z_state_is_no_longer_refused_WOULD_CROSS():
    """Stage A 2130z held UP 2.05 at a 0.46 basis and needed a DOWN leg.

    It settled -$0.9430 stranded. Under the defect every DOWN completion on
    this route was refused WOULD_CROSS, so the leg could not be paired.
    """
    r = _decide("0.69", "0.70", "UP", "2.05", "0.943")
    assert r.get("reason") != "WOULD_CROSS", r
    assert "WOULD_CROSS" not in str(r.get("reason") or "")


@pytest.mark.parametrize("bid,ask", [("0.69", "0.70"), ("0.80", "0.81"),
                                     ("0.90", "0.91"), ("0.44", "0.45")])
def test_no_DOWN_completion_at_the_touch_is_refused_WOULD_CROSS(bid, ask):
    r = _decide(bid, ask, "UP", "2.05", "0.943")
    assert r.get("reason") != "WOULD_CROSS", (bid, ask, r)


def test_an_UP_completion_that_crosses_is_still_refused():
    """2145z's refusal was GENUINE and must stay refused.

    Held DOWN at 0.45 with UP collapsed to 0.10: buying UP at the ask is a
    cross, and maker-only must continue to refuse it.
    """
    r = _decide("0.09", "0.10", "DOWN", "2", "0.90", depth_ticks=0)
    # either refused as WOULD_CROSS, or priced on the maker side -- never
    # silently crossing
    if r.get("ok"):
        leg = r.get("leg") or {}
        assert BC._would_cross("UP", D(leg["price"]), D("0.09"),
                               D("0.10")) is False
    else:
        assert r.get("reason") is not None


# ------------------------------- the pair cost, which the same bug corrupted

def test_the_DOWN_pair_cost_is_basis_plus_own_not_basis_plus_wire():
    """`complement_order` computes `pair = paid + px`; decide() must agree.

    Under the defect a DOWN complement's pair was `avg + (1 - px)`, so the
    2130z state read 1.37 instead of 0.55 and tripped PAIR_ABOVE_REDEMPTION
    independently of the cross check.
    """
    r = _decide("0.69", "0.70", "UP", "2.05", "0.943")
    assert r.get("pair_own_cost") is not None, r
    pair = D(r["pair_own_cost"])
    # held UP 2.05 for $0.943 -> basis 0.46; DOWN at the touch -> own 0.30
    assert pair < D(1), r
    assert D("0.70") < pair < D("0.80"), r
    assert r.get("reason") != "PAIR_ABOVE_REDEMPTION", r


def test_the_2130z_completion_locks_a_non_negative_pnl():
    r = _decide("0.69", "0.70", "UP", "2.05", "0.943")
    assert r["ok"] is True, r
    assert D(r["locked_pnl_usd"]) >= 0, r


def test_a_genuinely_dear_DOWN_pair_is_still_refused_above_redemption():
    """The fix must not let a certain loss through."""
    # held UP 2 at a 0.95 basis; DOWN at the touch on a 0.20/0.21 book is
    # own 0.79, so the pair is 1.74 and must be refused.
    r = _decide("0.20", "0.21", "UP", "2", "1.90")
    assert r["ok"] is False
    # refused either by `complement_order`'s own cap upstream or by step 7's
    # redemption line here. Which layer catches it is not the contract; that
    # a certain loss is refused IS.
    assert r.get("reason") in (
        "PAIR_ABOVE_REDEMPTION", "ECONOMICS:COMPLEMENT_PAIR_ABOVE_CAP"), r
    if r.get("pair_own_cost") is not None:
        assert D(r["pair_own_cost"]) > D(1)
