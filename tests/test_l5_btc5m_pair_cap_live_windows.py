"""The completion pair-cost cap, at the states of the two live windows of
2026-11-21. It pins what the EXISTING switch does there. It does not say the
cap is profitable: see analysis/l5/btc5m/PAIR_COST_GATE_TWO_WINDOWS_RESULT.txt
-- refusing these completions leaves the first leg unmatched, and in the
14:20Z window that would have lost more than completing did.
"""
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

CAP = dict(straddle_max_completed_pair_cost=D("0.98"))

#: 14:20Z after five fills: UP 0.17 for 0.0709, DOWN 0.24 for 0.1424
#: (average 0.5933). UP then traded 0.93-0.97.
W2 = inv("0.17", "0.0709", "0.24", "0.1424")
#: 06:20Z after the first fill: DOWN 0.08 at 0.40. UP then traded 0.66-0.82.
W1 = inv("0", "0", "0.08", "0.032")


def up_price(r):
    got = legs(r, C.UP)
    return None if not got else D(str(got[0]["price"]))


@pytest.mark.parametrize("bid,ask", [("0.94", "0.95"), ("0.93", "0.94"),
                                     ("0.97", "0.98")])
def test_1420z_without_the_cap_up_is_bid_at_the_book(bid, ask):
    r = decide(W2, bid, ask, t=85.0)
    assert up_price(r) == D(bid)                 # 0.59 + 0.94 = 1.53 a pair


@pytest.mark.parametrize("bid,ask", [("0.94", "0.95"), ("0.93", "0.94"),
                                     ("0.97", "0.98"), ("0.71", "0.72")])
def test_1420z_with_the_cap_up_is_never_bid_above_what_keeps_the_pair_in_cap(
        bid, ask):
    r = decide(W2, bid, ask, t=85.0, **CAP)
    plan = r["evaluation"]["pair_completion"]
    assert plan["held_side"] == C.DOWN and plan["completing_side"] == C.UP
    assert plan["action"] == "RESTS_AT_CAP"
    assert D(plan["cap_price"]) == D("0.38")     # 0.98 - 0.5933, floored
    p = up_price(r)
    assert p is None or p <= D("0.38")
    assert p is None or p + D("0.1424") / D("0.24") <= D("0.98")


@pytest.mark.parametrize("bid,ask", [("0.66", "0.67"), ("0.79", "0.80"),
                                     ("0.82", "0.83")])
def test_0620z_with_the_cap_the_up_completions_are_priced_down(bid, ask):
    r = decide(W1, bid, ask, t=45.0, **CAP)
    plan = r["evaluation"]["pair_completion"]
    assert D(plan["cap_price"]) == D("0.58")     # 0.98 - 0.40
    p = up_price(r)
    assert p is None or p <= D("0.58")


def test_the_cap_adds_no_order_and_never_raises_a_price():
    for held, bid, ask, t in ((W2, "0.94", "0.95", 85.0),
                              (W1, "0.79", "0.80", 45.0)):
        off = decide(held, bid, ask, t=t)
        on = decide(held, bid, ask, t=t, **CAP)
        assert len(legs(on)) <= len(legs(off))
        for o in legs(on):
            same = [x for x in legs(off) if x["side"] == o["side"]]
            assert same and D(str(o["price"])) <= D(str(same[0]["price"]))
