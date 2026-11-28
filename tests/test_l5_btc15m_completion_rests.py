"""A pair completion must rest, and must not pay more than the book asks.

THE DEFECT, measured 2026-11-19 against the production controller.

`cprice` is `pair_target - our_average_cost`. It reads NOTHING from the book.
When the completion side's own offer sits below it, the controller posted at
the ceiling with `post_only=True` -- an order through the offer, which the
venue rejects outright. The completion never rested, never filled, and the
residual it exists to convert stayed residual.

    held UP at 0.30, DOWN own ask 0.40, target 0.96
      -> posted DOWN at 0.66   CROSSING, post_only, unfillable
      -> the same pair was available to a MAKER at DOWN's own bid of 0.38,
         a pair cost of 0.68 against the 0.96 it asked for

WHY THIS IS THE HIGHEST-IMPACT DEFECT. Reconstructed from 228,561 confirmed
BTC15m fills and 2,041 REDEEM settlements of competitor 0x8dxd
(`analysis/l5/btc15m/competitor_postfill.py`): their matched-pair P&L is
+$160,699 and their residual P&L is -$144,048. Matched pairs are the entire
source of their profit, they acquire them at a median pair cost of 0.9189,
and 31.1% of their post-fill decisions are an opposite-side buy. Our
controller could not complete a pair at all unless it happened to be posting
a price that rested by luck.

THE FIX. Cap the completion one tick inside the completion side's own offer
whenever the ceiling would cross it. The order then rests, and it pays
strictly LESS than the ceiling, so the achieved pair cost can only improve.
It is not a taker route: it never reaches the offer.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_controller as C          # noqa: E402

TICK = D("0.01")


def params(**kw):
    base = dict(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"),
        clip_usd=D("1.00"), hedge_enabled=False, reserve_mode="NONE",
        max_exposure_usd=D("5.00"),
        straddle_p_pair_natural=D("0.070"), straddle_p_complete=D("0.131"),
        straddle_p_lone_win=D("0.0981"),
        adverse_selection_per_share=D("0.06"),
        straddle_repeat_enabled=True,
        straddle_max_side_usd=D("2.00"),
        straddle_max_unmatched_usd=D("1.00"),
        straddle_completion_pair_cost=D("0.96"))
    base.update(kw)
    return C.Params(**base)


def held(up_qty="1.00", up_cost="0.30", dn_qty="0", dn_cost="0"):
    return {C.UP: {"qty": up_qty, "cost": up_cost},
            C.DOWN: {"qty": dn_qty, "cost": dn_cost}}


def decide(bid, ask, inv=None, t=120.0, resting=(), **kw):
    return C.decide(book={"bid": bid, "ask": ask, "age_s": 0.1},
                    t_in_window_s=t, inventory=inv or held(),
                    resting=list(resting), params=params(**kw))


def completion(res):
    return next((o for o in res["orders"]
                 if o.get("reason") == "STRADDLE_COMPLETION"), None)


def own(side, bid, ask):
    """(own bid, own ask) for `side`, in that side's own price terms."""
    b, a = D(bid), D(ask)
    return (b, a) if side == C.UP else (D("1") - a, D("1") - b)


# ------------------------------------------------- it rests

@pytest.mark.parametrize("bid,ask", [
    ("0.46", "0.48"),       # DOWN own ask 0.54, ceiling 0.66 -> crossed
    ("0.60", "0.62"),       # DOWN own ask 0.40, ceiling 0.66 -> crossed hard
    ("0.70", "0.72"),       # DOWN own ask 0.30
    ("0.90", "0.92"),       # DOWN own ask 0.10
])
def test_a_completion_never_crosses_the_offer(bid, ask):
    """THE DEFECT, as one assertion. post_only + crossing = rejected, so a
    crossing completion is not a completion at all."""
    o = completion(decide(bid, ask))
    assert o is not None, "a completion must still be offered"
    _, own_ask = own(C.DOWN, bid, ask)
    assert D(str(o["price"])) < own_ask, "would take, not rest"
    assert o.get("post_only") is True


@pytest.mark.parametrize("bid,ask", [("0.60", "0.62"), ("0.46", "0.48")])
def test_the_capped_price_is_one_tick_inside_the_offer(bid, ask):
    o = completion(decide(bid, ask))
    _, own_ask = own(C.DOWN, bid, ask)
    assert D(str(o["price"])) == own_ask - TICK


# ------------------------------- it pays less, never more

def test_capping_strictly_improves_the_pair_cost():
    """Holding UP at 0.30 with DOWN's own ask at 0.40: the ceiling asked
    0.96 for the pair; resting inside the offer gets it for 0.69."""
    o = completion(decide("0.60", "0.62"))
    pair = D("0.30") + D(str(o["price"]))
    assert pair == D("0.69")
    assert pair < D("0.96")


def test_it_never_pays_more_than_the_target():
    for bid, ask in (("0.40", "0.42"), ("0.46", "0.48"), ("0.60", "0.62")):
        o = completion(decide(bid, ask))
        assert D("0.30") + D(str(o["price"])) <= D("0.96"), (bid, ask)


def test_a_malformed_resting_order_still_refuses_the_whole_decision():
    """Unrelated to the fix, but it is what caught my bad fixture above:
    one unreadable resting order cancels everything and trades nothing."""
    r = decide("0.60", "0.62",
               resting=[{"id": "X1", "side": C.DOWN, "price": D("0.39"),
                         "qty": D("1.00")}])          # no age_s
    assert r["why"] == "RESTING_UNREADABLE"
    assert r["orders"] == [{"op": "CANCEL", "id": "X1"}]


def test_an_already_resting_ceiling_is_left_exactly_alone():
    """NOT A REWRITE OF THE PRICING RULE. When the ceiling already rests
    below the offer, the price must be byte-identical to before the fix.

    MY FIRST VERSION OF THIS TEST WAS WRONG and the code was right: I held
    UP at 0.30 against DOWN's own ask of 0.60, which makes the ceiling 0.66
    -- still crossing, so of course it was capped. Holding UP at 0.40 puts
    the ceiling at 0.56, genuinely below the offer, which is the case this
    test is actually about."""
    inv = held(up_cost="0.40")
    o = completion(decide("0.40", "0.42", inv=inv))   # DOWN own ask 0.60
    assert D(str(o["price"])) == D("0.56")             # 0.96 - 0.40, untouched
    assert D(str(o["price"])) < own(C.DOWN, "0.40", "0.42")[1]


def test_the_cap_is_reported_as_evidence():
    ev = decide("0.60", "0.62")["evaluation"]["completion"]
    assert ev["maker_capped_from"] == "0.66"
    assert ev["maker_cap_why"] == "CEILING_CROSSED_THE_OFFER"
    assert ev["price"] == "0.39"
    assert ev["pair_cost"] == "0.69"


def test_no_cap_is_reported_when_none_was_applied():
    ev = decide("0.40", "0.42",
                inv=held(up_cost="0.40"))["evaluation"]["completion"]
    assert "maker_capped_from" not in ev


# --------------------------- it recognises its own working order

def test_a_working_capped_completion_is_not_replaced():
    """If the dedup check did not know the capped price, the controller
    would cancel and re-place its OWN completion every step and lose its
    queue place each time."""
    o = completion(decide("0.60", "0.62"))
    px = D(str(o["price"]))
    again = decide("0.60", "0.62",
                   resting=[{"id": "X1", "side": C.DOWN, "price": px,
                             "qty": D("1.00"), "age_s": 1.0}])
    assert completion(again) is None, "it is already working"
    assert not [x for x in again["orders"]
                if x.get("op") == "CANCEL" and x.get("id") == "X1"]


# ------------------- the other side of the book works the same way

def test_it_works_when_the_held_side_is_DOWN():
    inv = held(up_qty="0", up_cost="0", dn_qty="1.00", dn_cost="0.30")
    o = completion(decide("0.38", "0.40", inv=inv))
    assert o["side"] == C.UP
    _, up_ask = own(C.UP, "0.38", "0.40")
    assert D(str(o["price"])) < up_ask
    assert D("0.30") + D(str(o["price"])) <= D("0.96")


# ----------------------------- every existing guard still binds

def test_completion_stays_off_by_default():
    """The fix must not switch the behaviour on. Production default is
    straddle_completion_pair_cost = 0, which is COMPLETION_DISABLED."""
    r = decide("0.60", "0.62", straddle_completion_pair_cost=D("0"))
    assert completion(r) is None
    assert r["evaluation"]["completion"]["why"] == "COMPLETION_DISABLED"


def test_a_stale_book_cannot_be_completed_against():
    r = C.decide(book={"bid": "0.60", "ask": "0.62", "age_s": 99.0},
                 t_in_window_s=120.0, inventory=held(), resting=[],
                 params=params())
    assert completion(r) is None


def test_the_exposure_cap_still_binds():
    r = decide("0.60", "0.62", max_exposure_usd=D("0.31"))
    assert completion(r) is None


def test_completion_is_still_allowed_after_the_entry_cutoff():
    """Entry stops late in the window; COMPLETION must not, or a residual
    acquired at t=260 could never be converted."""
    r = decide("0.60", "0.62", t=280.0, no_new_entry_after_s=270.0)
    o = completion(r)
    assert o is not None
    assert not [x for x in r["orders"] if x.get("reason") == "STRADDLE_LEG"]


def test_no_completion_without_held_inventory():
    flat = held(up_qty="0", up_cost="0")
    assert completion(decide("0.60", "0.62", inv=flat)) is None


def test_the_order_is_a_buy_on_the_opposite_side():
    o = completion(decide("0.60", "0.62"))
    assert o["side"] == C.DOWN and o["intent"] == "BUY"
    assert o["op"] == "PLACE"


# --------------- taker remains a separate, unauthorized route

def test_the_cap_is_not_a_taker_route():
    """It rests one tick INSIDE the offer, so it cannot take. And with
    taker_allowed False nothing may cross regardless."""
    o = completion(decide("0.60", "0.62", taker_allowed=False))
    _, own_ask = own(C.DOWN, "0.60", "0.62")
    assert D(str(o["price"])) < own_ask
    assert o.get("post_only") is True


def test_the_capped_price_never_reaches_the_offer_at_any_book():
    for b in range(5, 95, 7):
        bid = D(b) / D("100")
        ask = bid + TICK
        o = completion(decide(str(bid), str(ask)))
        if o is None:
            continue
        _, own_ask = own(C.DOWN, str(bid), str(ask))
        assert D(str(o["price"])) < own_ask, (bid, ask, o["price"])
        assert o.get("post_only") is True
