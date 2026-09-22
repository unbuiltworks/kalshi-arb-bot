"""V2 differs from V1 in exactly one thing: which side the complement is.

These tests are about ORIENTATION. A policy that looks up the wrong side of
the book produces perfectly plausible numbers, which is why the defect
survived two rounds of results, so the orientation is asserted directly rather
than inferred from an outcome.
"""
from fractions import Fraction as F

import pytest

from src.gemini_execution import ev_policy as V1
from src.gemini_execution import ev_policy_v2 as V2

YES, NO = V1.YES, V1.NO


def _state(yes_prices=(), no_prices=()):
    """A tape where the two sides have deliberately DIFFERENT price levels, so
    that reading the wrong one cannot coincidentally give the right answer."""
    st = V1.MarketState()
    t = 0
    for p in yes_prices:
        t += 1
        st.observe(t, "sell", F(p))           # a sell print makes YES buyable
    for p in no_prices:
        t += 1
        st.observe(t, "buy", F(1) - F(p))     # a buy print makes NO buyable
    return st


FLAT = {"yes_qty": 0, "no_qty": 0, "avg_yes": None, "avg_no": None}


def test_the_complement_of_each_side():
    assert V2.complement_of(YES) == NO
    assert V2.complement_of(NO) == YES
    with pytest.raises(ValueError):
        V2.complement_of("BOTH")


def test_v2_prices_the_side_it_would_still_have_to_buy():
    st = _state(yes_prices=[F(30, 100)] * 4, no_prices=[F(55, 100)] * 4)
    d = V2.decide(inventory=FLAT, offered_side=YES, price=F(30, 100), state=st)
    assert d["need_side"] == NO
    assert d["q_hat"] == F(55, 100)          # the NO side's own mean, not YES's


def test_v1_prices_the_side_it_is_already_being_offered():
    """The defect, pinned so it cannot be 'fixed' by accident: V1's q_hat is
    the OFFERED side's mean. Changing this is changing published results."""
    st = _state(yes_prices=[F(30, 100)] * 4, no_prices=[F(55, 100)] * 4)
    d = V1.decide(inventory=FLAT, offered_side=YES, price=F(30, 100), state=st)
    assert d["q_hat"] == F(30, 100)


def test_the_two_disagree_whenever_the_sides_are_priced_differently():
    st = _state(yes_prices=[F(20, 100)] * 3, no_prices=[F(70, 100)] * 3)
    a = V1.decide(inventory=FLAT, offered_side=YES, price=F(20, 100), state=st)
    b = V2.decide(inventory=FLAT, offered_side=YES, price=F(20, 100), state=st)
    assert a["q_hat"] != b["q_hat"]
    # V1 projects a 0.40 pair here and V2 a 0.90 one; only the second is a
    # projection of what pairing would actually cost.
    assert a["q_hat"] + F(20, 100) == F(40, 100)
    assert b["q_hat"] + F(20, 100) == F(90, 100)


def test_orientation_is_symmetric_for_a_no_offer():
    st = _state(yes_prices=[F(25, 100)] * 3, no_prices=[F(60, 100)] * 3)
    d = V2.decide(inventory=FLAT, offered_side=NO, price=F(60, 100), state=st)
    assert d["need_side"] == YES and d["q_hat"] == F(25, 100)


def test_completion_is_identical_in_both_versions():
    """Only acquisition was oriented wrongly. A completion decision reads the
    leg already held and the price on offer, and neither version consults the
    tape for it."""
    inv = {"yes_qty": 2, "no_qty": 0, "avg_yes": "0.30", "avg_no": None}
    st = _state(yes_prices=[F(30, 100)], no_prices=[F(70, 100)])
    a = V1.decide(inventory=inv, offered_side=NO, price=F(60, 100), state=st)
    b = V2.decide(inventory=inv, offered_side=NO, price=F(60, 100), state=st)
    for k in ("action", "complete_value", "hold_value", "edge"):
        assert a[k] == b[k]


def test_v2_copies_nothing_it_could_get_wrong():
    """The residual table, p_win and the value functions are V1's objects. If
    a copy ever appears, the two policies could drift apart for reasons other
    than orientation and the comparison would stop meaning anything."""
    assert V2.p_win is V1.p_win
    assert V2.RESIDUAL_RETURN is V1.RESIDUAL_RETURN
    assert V2.acquire_value is V1.acquire_value
    assert V2.complete_value is V1.complete_value
    assert V2.MarketState is V1.MarketState
