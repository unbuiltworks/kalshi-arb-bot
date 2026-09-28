"""3b: the q1-executor deltas must be default-identical and never looser.

Two deltas from 03f881ca touch risk bounds, so labelling them is not enough:

  1. `taker_fee`/`maker_rebate` stopped using local coefficients and now read
     `src/pmus_fees.py`. The taker coefficient moved 0.06 -> 0.0695 (PMUS
     republished it effective 2026-09-17). A LARGER cost can only TIGHTEN a
     fee-safe bound, which is the safe direction; these tests prove the sign.
  2. `complement_bound` gained a `tick` parameter. Omitting it must reproduce
     the reviewed behaviour byte-for-byte, and passing a coarser grid must
     never return a HIGHER (looser) bound.
"""
from __future__ import annotations

import os
import sys
from decimal import ROUND_DOWN, Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_q1_executor as Q                          # noqa: E402

PRICES = [D(x) for x in ("0.0100", "0.1000", "0.2500", "0.4400", "0.5000",
                         "0.6600", "0.7900", "0.9000", "0.9900")]


def _bound_as_reviewed(first_cost: D, min_edge: D = None) -> D:
    """The 03f881ca implementation, copied verbatim."""
    min_edge = Q.MIN_EDGE if min_edge is None else min_edge
    px = (Q.ONE - D(str(first_cost)) - min_edge)
    px = (px / Q.TICK).to_integral_value(rounding=ROUND_DOWN) * Q.TICK
    if px < Q.MIN_PRICE or px > Q.MAX_PRICE:
        return None
    return px


def test_omitting_tick_reproduces_the_reviewed_bound_exactly():
    for c in PRICES:
        assert Q.complement_bound(c) == _bound_as_reviewed(c), str(c)


def test_a_coarser_grid_is_never_looser():
    """Rounding DOWN means a coarser grid can only lower the max complement."""
    for c in PRICES:
        fine = Q.complement_bound(c)
        coarse = Q.complement_bound(c, tick=D("0.01"))
        if fine is None or coarse is None:
            continue
        assert coarse <= fine, (str(c), str(coarse), str(fine))


def test_a_degenerate_grid_refuses_rather_than_defaulting():
    for bad in (D(0), D("-0.01"), D(1), D("2")):
        assert Q.complement_bound(D("0.40"), tick=bad) is None, str(bad)


def test_the_taker_coefficient_in_force_is_0_0695_not_0_06():
    """The stale local 0.06 understated the cost by 15.8%."""
    for p in PRICES:
        now = Q.taker_fee(p)
        stale = (D("0.06") * p * (Q.ONE - p)).quantize(D("0.0001"))
        assert now >= stale, (str(p), str(now), str(stale))
    mid = Q.taker_fee(D("0.50"))
    assert mid == (D("0.0695") * D("0.50") * D("0.50")).quantize(D("0.0001"))


def test_the_larger_taker_fee_can_only_tighten_taker_completion():
    """A cost that went UP must never admit a completion it used to refuse."""
    def ok_stale(first, ask, min_edge=None):
        me = Q.MIN_EDGE if min_edge is None else min_edge
        fee = (D("0.06") * ask * (Q.ONE - ask)).quantize(D("0.0001"))
        return (D(str(first)) + D(str(ask)) + fee) <= (Q.ONE - me)
    admitted_now_refused_then = 0
    for first in PRICES:
        for ask in PRICES:
            now = Q.taker_completion_ok(first, ask)
            then = ok_stale(first, ask)
            if now and not then:
                admitted_now_refused_then += 1
    assert admitted_now_refused_then == 0


def test_the_rebate_is_still_reported_positive_and_never_netted():
    for p in PRICES:
        assert Q.maker_rebate(p) >= 0


def test_the_hard_caps_that_make_this_a_canary_are_untouched():
    """Whatever else moved, the q=1/\\$1 envelope must still be constants."""
    assert Q.MIN_BUYING_POWER == D("2.00")
    assert Q.TICK > 0 and Q.MIN_PRICE > 0 and Q.MAX_PRICE < Q.ONE
