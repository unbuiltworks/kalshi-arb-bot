"""The no-fill forensics must not turn an upper bound into a promise.

The claim it makes -- that ~24% of unfilled completes had an L5-consistent
reprice the book would have met -- is the most load-bearing number in the
current research, and it rests on extrema over a window rather than a time
series. These tests pin the arithmetic, and pin that the bound is labelled
a bound everywhere it is published.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_complete_no_fill_forensics as nf

_SRC = open(nf.__file__).read()
_LOGS = "../r5f_logs"
_have_logs = os.path.isdir(_LOGS)
_paths = ([os.path.join(_LOGS, f) for f in sorted(os.listdir(_LOGS))]
          if _have_logs else [])


def _order(px, l5, min_opp, max_touch, *, outcome="no_fill_cancelled",
           filled="0", rest_s=30.0):
    return {
        "relation": "complete", "order_id": "X", "slug": "s",
        "stage": "spring_complete_1", "side": "NO", "px": str(px),
        "qty": "5", "filled_qty": filled, "rest_s": rest_s,
        "end_reason": "deadline" if outcome == "no_fill_cancelled" else "fill",
        "outcome": outcome, "l5_touch_px": str(l5),
        "rest_book": {"min_opp_ask": str(min_opp), "max_touch": str(max_touch),
                      "polls": 27},
    }


# --------------------------------------------------------------- arithmetic
def test_distance_is_measured_from_the_books_closest_approach():
    c = nf.classify(_order("0.51", "0.51", "0.54", "0.51"))
    assert c["closest_book_gap_ticks"] == 3
    assert c["book_gap_vs_l5_touch_ticks"] == 3
    assert c["placement_offset_vs_l5_ticks"] == 0


def test_a_reprice_counts_only_when_l5s_touch_moved_above_us():
    # touch stayed where we were: no reprice existed
    assert nf.classify(_order("0.51", "0.51", "0.52", "0.51"))[
        "l5_reprice_existed"] is False
    # touch moved up one tick: a reprice existed
    c = nf.classify(_order("0.51", "0.51", "0.52", "0.52"))
    assert c["l5_reprice_existed"] is True
    assert c["l5_touch_drift_ticks"] == 1


def test_the_book_must_actually_reach_the_new_touch():
    # touch moved to 0.52, book only ever got to 0.54 -> not reached
    assert nf.classify(_order("0.51", "0.51", "0.54", "0.52"))[
        "book_reached_that_touch"] is False
    # touch moved to 0.52 and the book got to 0.52 -> reached
    assert nf.classify(_order("0.51", "0.51", "0.52", "0.52"))[
        "book_reached_that_touch"] is True


def test_reaching_without_a_reprice_is_not_counted_as_a_reprice_opportunity():
    """The book coming to our own price is a queue loss, not a missed move."""
    c = nf.classify(_order("0.51", "0.51", "0.50", "0.51"))
    assert c["closest_book_gap_ticks"] == -1
    assert c["l5_reprice_existed"] is False
    assert c["book_reached_that_touch"] is False


def test_profile_counts_the_two_conditions_independently():
    orders = [
        _order("0.51", "0.51", "0.52", "0.52"),   # reprice + reached
        _order("0.51", "0.51", "0.54", "0.52"),   # reprice, not reached
        _order("0.51", "0.51", "0.54", "0.51"),   # no reprice
    ]
    p = nf.profile(orders, "no_fill_cancelled")
    assert p["orders"] == 3
    assert p["l5_reprice_existed"] == 2
    assert p["and_book_reached_that_touch"] == 1
    assert p["reprice_opportunity_upper_bound_share"] == pytest.approx(1 / 3,
                                                                      abs=1e-4)


def test_orders_without_a_rest_book_are_dropped_not_defaulted():
    bare = _order("0.51", "0.51", "0.52", "0.52")
    bare.pop("rest_book")
    assert nf.profile([bare], "no_fill_cancelled")["orders"] == 0


# ------------------------------------------------------ the honesty of it
def test_the_bound_is_named_a_bound_in_the_key_the_docstring_and_the_report():
    assert "upper_bound" in "".join(
        nf.profile([_order("0.51", "0.51", "0.52", "0.52")],
                   "no_fill_cancelled").keys())
    assert "UPPER BOUND" in nf.__doc__


def test_it_is_classified_as_fidelity_not_as_a_new_strategy():
    assert "BEHAVIORAL-FIDELITY FIX" in _SRC
    assert "not a new strategy" in _SRC
    # the distinction the owner drew: follow L5's price, do not chase fills
    assert "fill-chaser" in nf.__doc__


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "post(",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_touch_the_frozen_completion_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert frozen not in _SRC, frozen


# ------------------------------------------------------------- against real logs
@pytest.mark.skipif(not _have_logs, reason="harvested logs not present")
def test_the_real_population_separates_filled_from_unfilled_by_distance():
    rep = nf.report(_paths)
    u = rep["unfilled"]["closest_book_gap_ticks"]
    f = rep["filled_for_contrast"]["closest_book_gap_ticks"]
    assert u["n"] > 300 and f["n"] > 50
    # the whole finding: unfilled sit further from the touch
    assert u["median"] > f["median"]
    assert u["p75"] > f["p75"]


@pytest.mark.skipif(not _have_logs, reason="harvested logs not present")
def test_the_reprice_bound_is_a_subset_of_the_reprices_that_existed():
    rep = nf.report(_paths)
    u = rep["unfilled"]
    assert u["and_book_reached_that_touch"] <= u["l5_reprice_existed"]
    assert u["l5_reprice_existed"] <= u["orders"]
    assert 0.0 < u["reprice_opportunity_upper_bound_share"] < 1.0


@pytest.mark.skipif(not _have_logs, reason="harvested logs not present")
def test_placement_is_faithful_so_the_defect_is_not_the_opening_price():
    rep = nf.report(_paths)
    assert rep["unfilled"]["placed_at_l5_touch_share"] > 0.6


# ------------------------------------- the mechanism gap, read from the source
_LIVE = "src/pmus_spring_l5_session.py"


def test_the_requote_condition_still_excludes_complete_by_default():
    """The claim rests on the LIVE path's effective behaviour, so read it.

    The condition is now `relation_ in requote_relations()`, which returns
    ("open", "extend") unless an owner arms PMUS_SPRING_COMPLETE_REQUOTE. The
    forensics prose below describes a tape produced with COMPLETE excluded,
    and it stays true for as long as that is the DEFAULT. If the default ever
    flips this test fails, which is exactly when the prose stops being true."""
    from src import pmus_spring_l5_session as ses
    src = open(_LIVE).read()
    assert "if relation_ in requote_relations() and touch != px_:" in src
    assert 'return f"touch_moved:{touch}"' in src
    assert ses.DEFAULT_COMPLETE_REQUOTE is False
    assert ses.requote_relations() == ("open", "extend")


def test_the_crossing_guard_runs_ahead_of_the_touch_check_for_all_relations():
    """Why 'repricing could cross' is not an objection to the proposal."""
    src = open(_LIVE).read()
    cross = src.index('return f"would_cross:{opp_ask}"')
    touch = src.index('return f"touch_moved:{touch}"')
    assert cross < touch, "the crossing guard must precede the touch check"
    # and it is not gated on relation
    guard = src[src.index('if px_ >= D(str(opp_ask)):'):cross]
    assert "relation_" not in guard


def test_the_forensics_states_the_gap_without_claiming_an_effect_size():
    rep = nf.report(_paths) if _have_logs else None
    if rep is None:
        pytest.skip("harvested logs not present")
    m = rep["the_mechanism_already_exists"]
    assert "0 of 868" in m and "36 OPEN" in m
    assert "confounded by relation" in m
    assert "NOT an effect size" in m


@pytest.mark.skipif(not _have_logs, reason="harvested logs not present")
def test_no_complete_ever_exited_by_reprice_in_the_tape():
    from src import l5_completion_economics as ce
    orders = ce.all_orders(_paths)
    by = {"open": 0, "extend": 0, "complete": 0}
    for o in orders:
        if str(o.get("end_reason") or "").startswith("repriced:touch_moved"):
            by[o.get("relation")] = by.get(o.get("relation"), 0) + 1
    assert by["complete"] == 0
    assert by["open"] > 0 and by["extend"] > 0
