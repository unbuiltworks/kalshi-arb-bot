"""An unreadable queue is UNKNOWN: not a non-fill, and not an empty queue.

The defect this pins: `rung_depth` returns None both for a price the ladder
does not carry and for a price with nothing resting, and `queue_fill` used
to report that as `filled: False` with QUEUE_NEVER_CLEARED -- an assertion
about a queue never read. Because a <= 0.96 pair must quote outside the few
displayed rungs, the strategy under test was structurally assigned zero
fills, and those non-fills were then counted as evidence.
"""
from decimal import Decimal as D

import src.l5_shadow_policy_eval as P


TRADES = [{"t": 10.0, "px": "0.59", "qty": "5000"},
          {"t": 11.0, "px": "0.58", "qty": "5000"}]


# ------------------------------------------------ the leg-level contract

def test_an_unreadable_queue_is_UNKNOWN_not_NOT_FILLED():
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=None)
    assert f["filled"] is False
    assert f["fill_known"] is False
    assert f["reason"] == "QUEUE_AHEAD_UNREADABLE"
    assert P.fill_state(f) == P.FILL_UNKNOWN


def test_UNKNOWN_is_NOT_treated_as_an_empty_queue():
    """The opposite error. Enormous qualifying volume arrives and the leg
    STILL does not fill, because we had no information about the queue."""
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=None)
    assert f["filled"] is False
    assert f["cum_qualifying"] == "0"


def test_a_READ_queue_that_never_clears_is_a_real_NOT_FILLED():
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=D("1000000"))
    assert f["filled"] is False
    assert f["fill_known"] is True
    assert f["reason"] == "QUEUE_NEVER_CLEARED"
    assert P.fill_state(f) == P.FILL_NOT_FILLED


def test_a_cleared_queue_still_fills():
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=D("100"))
    assert f["filled"] is True
    assert P.fill_state(f) == P.FILL_FILLED
    assert f["fill_known"] is True


def test_an_explicit_zero_queue_is_KNOWN_and_fills():
    """A rung the ladder carries with zero depth is information, and it is
    different from a rung the ladder does not carry at all."""
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=D("0"))
    assert f["filled"] is True and f["fill_known"] is True


# ------------------------------------------------ fill_state itself

def test_fill_state_defaults_a_missing_record_to_UNKNOWN():
    assert P.fill_state(None) == P.FILL_UNKNOWN
    assert P.fill_state({}) == P.FILL_UNKNOWN


def test_fill_state_treats_a_legacy_record_without_the_flag_as_known():
    """Records written before the flag existed carried a read queue."""
    assert P.fill_state({"filled": False}) == P.FILL_NOT_FILLED


# ------------------------------------------------ rung_depth is the source

def test_rung_depth_returns_None_for_a_rung_the_ladder_lacks():
    snap = {"bids": [["0.96", "8000"]], "asks": [["0.97", "4000"]]}
    assert P.rung_depth(snap, "bids", D("0.96")) == D("8000")
    # the deep price a <= 0.96 PAIR actually needs is simply absent
    assert P.rung_depth(snap, "bids", D("0.59")) is None


def test_the_deep_pair_price_is_UNKNOWN_end_to_end():
    """This is the exact path that produced structural non-fills."""
    snap = {"bids": [["0.96", "8000"]], "asks": [["0.97", "4000"]]}
    q = P.rung_depth(snap, "bids", D("0.59"))
    f = P.queue_fill(TRADES, t_join=0.0, px=D("0.59"), side="bid",
                     queue_ahead=q)
    assert P.fill_state(f) == P.FILL_UNKNOWN
