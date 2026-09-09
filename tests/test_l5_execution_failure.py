"""The frozen A/B/C/D/E rules, on hand-computable cases.

Mutation-checked: flipping the B/C priority, reading a missing rest_book as
"quote never reached", choosing a constant instead of the derived R50,
accepting a bare plurality as dominance, and dropping the N3 ceiling each
turn this file red.
"""
import json

import pytest

from src import l5_execution_failure as ef


def order(**kw):
    base = {"relation": "complete", "px": "0.50", "filled_qty": "0",
            "end_reason": "cancelled:deadline", "rest_s": 30.0,
            "order_id": "X", "session_id": "s1", "t_utc": "2026-09-07T00:00:01",
            "rest_book": {"min_opp_ask": "0.55", "max_touch": "0.50",
                          "polls": 20}}
    base.update(kw)
    return base


# --- the evidence ceiling is part of the contract -------------------------
def test_category_a_is_named_for_the_quote_not_for_trades():
    """A must never claim "nobody traded"; that is not observable here."""
    assert ef.A_UNREACHED == "A_quote_never_reached"
    r = ef.classify(order(), r50=10.0)
    assert r["label"] == ef.A_UNREACHED
    assert "not proof no trade occurred" in r["why"]


def test_sampling_cadence_is_reported_so_the_bounds_stay_visible():
    r = ef.classify(order(rest_s=20.0, rest_book={"min_opp_ask": "0.55",
                                                  "polls": 10}), r50=5.0)
    assert r["cadence_s"] == pytest.approx(2.0)


# --- strict priority ------------------------------------------------------
def test_rejection_wins_over_everything():
    r = ef.classify(order(end_reason="rejected:not_enough_balance",
                          rest_book=None), r50=10.0)
    assert r["label"] == ef.D_REJECTED


def test_crossed_book_is_queue_even_when_we_cancelled_early():
    """B outranks C: a book that reached us and did not fill is our execution.

    Flipping this priority would relabel every crossed-book miss as a
    timeout and hide the queue problem entirely.
    """
    r = ef.classify(order(rest_s=1.0,
                          rest_book={"min_opp_ask": "0.50", "polls": 20}),
                    r50=30.0)
    assert r["label"] == ef.B_QUEUE


def test_ask_strictly_through_our_price_is_also_queue():
    r = ef.classify(order(rest_book={"min_opp_ask": "0.48", "polls": 9}),
                    r50=10.0)
    assert r["label"] == ef.B_QUEUE


def test_early_cancel_outranks_unrealistic_price():
    """C over A: blaming pricing for a timeout is the wrong diagnosis."""
    r = ef.classify(order(rest_s=4.0), r50=12.26)
    assert r["label"] == ef.C_EARLY


def test_waited_long_enough_and_book_never_came_is_A():
    r = ef.classify(order(rest_s=40.0), r50=12.26)
    assert r["label"] == ef.A_UNREACHED


# --- E fails closed, never into A ----------------------------------------
@pytest.mark.parametrize("rb", [None, {}, {"polls": 20},
                                {"min_opp_ask": "0.55", "polls": 4}])
def test_thin_or_missing_evidence_is_unknown_not_a(rb):
    """Reading absent evidence as "the quote never reached us" would load
    every unobserved order into A and invent a pricing problem."""
    r = ef.classify(order(rest_book=rb), r50=10.0)
    assert r["label"] == ef.E_UNKNOWN


def test_no_benchmark_means_unknown_rather_than_a_guess():
    r = ef.classify(order(rest_s=4.0), r50=None)
    assert r["label"] == ef.E_UNKNOWN


# --- R50 is derived, not chosen ------------------------------------------
def test_r50_comes_from_filled_completes_only():
    pop = [order(filled_qty="5", rest_s=10.0),
           order(filled_qty="5", rest_s=20.0),
           order(filled_qty="0", rest_s=999.0)]
    b = ef.rest_benchmark(pop)
    assert b["n"] == 2 and b["r50"] == pytest.approx(15.0)


def test_no_filled_completes_means_no_benchmark():
    b = ef.rest_benchmark([order(filled_qty="0")])
    assert b["r50"] is None and "cannot be separated" in b["why"]


# --- population ----------------------------------------------------------
def test_complements_without_a_first_fill_are_excluded_and_counted():
    orders = [
        order(relation="open", filled_qty="4", session_id="s1"),
        order(relation="complete", session_id="s1"),
        order(relation="complete", session_id="s2"),   # session never filled
    ]
    pop = ef.complements_after_first_fill(orders)
    assert len(pop["orders"]) == 1
    assert pop["excluded"]["session_never_filled_a_first_leg"] == 1


# --- the bars ------------------------------------------------------------
def test_underpowered_reports_deficit_and_computes_no_dominance():
    rows = [{"label": ef.A_UNREACHED, "cadence_s": 1.0} for _ in range(10)]
    res = ef.evaluate(rows)
    assert res["verdict"] == "UNDERPOWERED"
    assert res["deficit"] == ef.MIN_CLASSIFIED - 10
    assert "leader" not in res and "dominant" not in res


def test_evidence_ceiling_beats_everything_even_with_volume():
    rows = ([{"label": ef.E_UNKNOWN, "cadence_s": 1.0}] * 400 +
            [{"label": ef.B_QUEUE, "cadence_s": 1.0}] * 400)
    res = ef.evaluate(rows)
    assert res["verdict"] == "EVIDENCE_LIMITED"
    assert "dominant" not in res


def test_bare_plurality_is_not_dominance():
    """102 vs 98 out of 200 leads, but cannot clear the runner-up."""
    rows = ([{"label": ef.A_UNREACHED, "cadence_s": 1.0}] * 102 +
            [{"label": ef.B_QUEUE, "cadence_s": 1.0}] * 98)
    res = ef.evaluate(rows)
    assert res["verdict"] == "NO_DOMINANCE"
    assert "dominant" not in res


def test_a_real_majority_is_dominant_and_names_its_fix():
    rows = ([{"label": ef.B_QUEUE, "cadence_s": 1.0}] * 180 +
            [{"label": ef.A_UNREACHED, "cadence_s": 1.0}] * 40)
    res = ef.evaluate(rows)
    assert res["verdict"] == "DOMINANT"
    assert res["dominant"] == ef.B_QUEUE
    assert res["fix_implied"] == "queue positioning"


def test_wilson_lower_bound_is_below_the_point_estimate():
    lo, hi = ef.wilson(50, 100)
    assert lo < 0.5 < hi


# --- end to end on the real persisted R5F record -------------------------
def test_runs_on_the_real_sequence_and_stays_honest():
    res, rows = ef.run(["analysis/l5/r5f_day1_sequence.json"])
    assert res["population"]["complements_after_first_fill"] == len(rows)
    assert res["rest_benchmark"]["r50"] is not None
    # underpowered against N1, so no dominance may be claimed
    assert res["verdict"] == "UNDERPOWERED"
    assert "dominant" not in res
