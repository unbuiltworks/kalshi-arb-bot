"""Scoring the arms on INVENTORY, and the preregistered first-probe gate.

A generic maker fill rate is the wrong objective — chasing it produced seven
one-sided positions and −$102.54 of pure directional residual. These tests pin
that the score cannot report a two-sided rate without the pair cost beside it,
and that the gate's thresholds are the document's.
"""
import os
from decimal import Decimal as D

import pytest

import analysis.l5.pair_formation.arm_score as A
from src.l5_shadow import cohorts as CO

DOC = os.path.join(os.path.dirname(A.__file__),
                   "FIRST_PROBE_GATE_PREREGISTERED.md")
RESOLVED_UP = {"resolved": True, "winning_outcome": "Up",
               "resolution_source": "gamma_outcome_prices"}
OPEN = {"resolved": False, "resolution_source": "UNRESOLVED"}


def _o(outcome, qty, price, *, arm="D", t=1.0, market="m", venue="PMUS",
       cohort="BTC15m", fill=True):
    return {"arm": arm, "venue": venue, "market_key": market,
            "cohort": cohort, "desired_outcome": outcome,
            "quote_qty": str(qty), "quote_price": str(price),
            "evidenced_fill": fill, "placed_at": t, "intent_id": "i"}


def _w(outs, arm="D"):
    g = list(A.windows(outs, arm=arm).values())
    return g[0] if g else None


# ------------------------------------- only evidenced BTC15m rows aggregate

def test_only_evidenced_fills_enter():
    assert A.windows([_o("Up", 10, "0.45", fill=False)], arm="D") == {}
    assert A.windows([_o("Up", 10, "0.45", fill=None)], arm="D") == {}


def test_the_taker_regime_cohorts_are_excluded_not_averaged_in():
    """BTC5m is a different strategy; a taker's pair cost and a maker's are
    not the same quantity."""
    assert A.windows([_o("Up", 10, "0.45", cohort="BTC5m")], arm="D") == {}
    assert CO.is_maker_cohort("BTC5m") is False
    assert A.windows([_o("Up", 10, "0.45", cohort="BTC15m")], arm="D")


def test_the_arms_are_scored_from_their_own_rows_only():
    outs = [_o("Up", 10, "0.45", arm="R"), _o("Down", 10, "0.50", arm="D")]
    assert len(A.windows(outs, arm="R")) == 1
    assert len(A.windows(outs, arm="D")) == 1
    assert A.windows(outs, arm="S") == {}


# --------------------------------------------- the eight scored quantities

def test_a_two_sided_window_reports_every_required_quantity():
    g = _w([_o("Up", 10, "0.45", t=100.0), _o("Down", 10, "0.50", t=126.0)])
    r = A.score_window(g, RESOLVED_UP)
    assert r["two_sided"] is True
    assert r["time_to_two_sided_s"] == 26.0
    assert r["matched_qty"] == "10"
    assert r["effective_pair_cost"] == "0.950000"
    assert r["residual_qty"] == "0"
    assert r["settled_matched_pnl"] == "0.500000"
    assert r["settled_residual_pnl"] == "0.000000"


def test_a_one_sided_window_has_no_pair_cost_and_a_residual():
    g = _w([_o("Up", 10, "0.45", t=100.0)])
    r = A.score_window(g, RESOLVED_UP)
    assert r["two_sided"] is False and r["effective_pair_cost"] is None
    assert r["residual_qty"] == "10" and r["residual_side"] == "Up"
    assert r["time_to_two_sided_s"] is None


def test_an_unsettled_window_reports_no_pnl():
    g = _w([_o("Up", 10, "0.45"), _o("Down", 10, "0.50", t=2.0)])
    r = A.score_window(g, OPEN)
    assert r["state"] == "OPEN_UNSETTLED"
    assert r["settled_matched_pnl"] is None


# ------------------ formation and economics are reported together, always

def test_a_two_sided_rate_never_appears_without_the_pair_cost():
    rows = [A.score_window(_w([_o("Up", 10, "0.50", t=1.0),
                               _o("Down", 10, "0.93", t=2.0)]), RESOLVED_UP)]
    s = A.score_arm(rows, arm="D")
    assert s["P_two_sided_evidenced"] == 1.0
    assert s["effective_pair_cost"]["median"] == "1.430000"
    assert s["pairs_above_par"] == 1 and s["pairs_within_ceiling"] == 0


def test_an_arm_with_no_two_sided_window_says_so_rather_than_reporting_zero():
    rows = [A.score_window(_w([_o("Up", 10, "0.45")]), RESOLVED_UP)]
    s = A.score_arm(rows, arm="R")
    assert s["P_two_sided_evidenced"] == 0.0
    assert s["effective_pair_cost"] is None
    assert "is the finding" in s["note"]


def test_scoring_nothing_reports_none_not_zero():
    s = A.score_arm([], arm="S")
    assert s["P_two_sided_evidenced"] is None
    assert s["settled_matched_pnl"] is None


def test_l5s_own_record_is_the_benchmark_row():
    b = A.L5_BTC15M
    assert b["two_sided_rate"] == 0.960
    assert b["median_pair_cost_at_formation"] == "0.9900"
    assert b["median_pair_cost_final"] == "0.9624"
    assert b["maker_rate"] == 0.955


def test_the_report_splits_venues_and_says_they_qualify_independently():
    r = A.report("/nonexistent")
    assert "qualify independently" in r["labels"]
    assert "excluded, not deleted" in r["labels"]
    assert r["cohort"] == "BTC15m" and r["regime"] == "MAKER"


# ------------------------------------------------ the preregistered gate

def test_the_gate_document_exists_and_fixes_every_threshold():
    md = open(DOC).read()
    for token in ("≥ 20", "> $0", "median ≤ 0.96", "0 pairs above par",
                  "≤ 50 %", "0", "G-P1", "G-P7"):
        assert token in md


def test_the_gate_scopes_what_passing_authorizes():
    md = open(DOC).read()
    assert "does not authorize" in md
    assert "Live authorization is a separate human decision" in md
    assert "one market at a time" in md


def test_the_gate_lets_the_venues_qualify_independently():
    md = open(DOC).read()
    assert "qualify INDEPENDENTLY" in md
    assert "Neither waits for the other" in md


def test_the_gate_refuses_to_pool_arms_or_venues():
    md = open(DOC).read()
    assert "are **not** pooled" in md or "not** pooled" in md
    assert "no pooling across venues" in md


def test_the_gate_requires_the_matched_leg_to_stand_alone():
    """A total rescued by a lucky directional residual is not the mechanism."""
    md = open(DOC).read()
    assert "matched-pair P&L > 0 on its own" in md
    assert "lucky directional residual" in md
