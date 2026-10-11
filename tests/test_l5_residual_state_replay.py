"""The replay must keep predictive and realized features apart.

The number this module is written against is 36.8% -> 93.5% completion by
opening-side price count. Read naively it says "act more and you complete
more", which would license exactly the fill-chasing the owner ruled out.
These tests pin that the feature is labelled realized, that it is published
stratified by its confounders, and that a thin stratum reports no rate at
all rather than a loud one.
"""
from __future__ import annotations

import os

import pytest

from src import l5_completion_behavior as cb
from src import l5_residual_state_replay as rr

_SRC = open(rr.__file__).read()
_have = os.path.exists(cb.CANONICAL)


def _ev(completed, *, dur=5.0, tts=240.0, price=0.50, prices=1):
    return {"completed": completed, "duration_minutes": dur,
            "first_leg_tts": tts, "first_leg_price": price,
            "first_leg_distinct_prices": prices}


# ----------------------------------------------------------------- features
def test_remaining_fraction_is_time_left_over_window_length():
    assert rr.window_remaining_fraction(_ev(True, dur=5.0, tts=300.0)) == 1.0
    assert rr.window_remaining_fraction(_ev(True, dur=5.0, tts=60.0)) == 0.2
    assert rr.window_remaining_fraction(_ev(True, dur=None, tts=60.0)) is None


def test_imbalance_buckets_split_balanced_from_lopsided():
    assert rr.imbalance_bucket(_ev(True, price=0.50)).startswith("balanced")
    assert rr.imbalance_bucket(_ev(True, price=0.05)).startswith("lopsided")
    assert rr.imbalance_bucket(_ev(True, price=0.95)).startswith("lopsided")
    assert rr.imbalance_bucket(_ev(True, price=0.25)).startswith("leaning")


def test_the_two_feature_families_do_not_overlap():
    assert not set(rr.PREDICTIVE) & set(rr.REALIZED)
    # the one that cannot inform selection is on the realized side
    assert "opening_side_distinct_prices" in rr.REALIZED


# ------------------------------------------------------------------- counting
def test_conditional_rate_is_completed_over_n():
    ev = [_ev(True, price=0.50), _ev(False, price=0.50), _ev(True, price=0.05)]
    got = rr.conditional(ev, rr.imbalance_bucket)
    assert got["balanced 0.35-0.65"] == {"n": 2, "completed": 1,
                                         "completion_rate": 0.5}
    assert got["lopsided <0.20 or >0.80"]["completion_rate"] == 1.0


def test_a_thin_stratum_reports_counts_and_no_rate():
    ev = [_ev(True, prices=1)] * (rr.MIN_STRATUM_N - 1)
    cells = rr.stratified(ev, rr.opening_side_prices,
                          [rr.PREDICTIVE["window_length_minutes"],
                           rr.remaining_bucket])
    cell = next(iter(cells.values()))
    row = cell["1"]
    assert row["n"] == rr.MIN_STRATUM_N - 1
    assert row["completion_rate"] is None
    assert row["suppressed_below_min_n"] == rr.MIN_STRATUM_N


def test_a_stratum_at_the_threshold_does_report_a_rate():
    ev = [_ev(True, prices=1)] * rr.MIN_STRATUM_N
    cells = rr.stratified(ev, rr.opening_side_prices,
                          [rr.PREDICTIVE["window_length_minutes"],
                           rr.remaining_bucket])
    assert next(iter(cells.values()))["1"]["completion_rate"] == 1.0


def test_stratification_never_pools_across_cells():
    ev = ([_ev(True, dur=15.0, prices=1)] * 20
          + [_ev(False, dur=5.0, prices=1)] * 20)
    cells = rr.stratified(ev, rr.opening_side_prices,
                          [rr.PREDICTIVE["window_length_minutes"],
                           rr.remaining_bucket])
    rates = sorted(r["1"]["completion_rate"] for r in cells.values())
    assert rates == [0.0, 1.0]          # kept apart, not averaged to 0.5


def test_monotonicity_check_counts_only_usable_cells():
    cells = {"a": {"1": {"n": 20, "completed": 4, "completion_rate": 0.2},
                   "2": {"n": 20, "completed": 12, "completion_rate": 0.6}},
             "b": {"1": {"n": 20, "completed": 12, "completion_rate": 0.6},
                   "2": {"n": 20, "completed": 4, "completion_rate": 0.2}},
             "c": {"1": {"n": 2, "completed": 1, "completion_rate": None}}}
    m = rr._monotone(cells)
    assert m["strata_with_at_least_two_usable_cells"] == 2
    assert m["strata_where_the_direction_holds"] == 1
    assert m["share"] == 0.5


# -------------------------------------------------------------- the honesty
def test_the_realized_family_is_never_offered_as_a_filter():
    rep_text = _SRC
    assert "NOT offered as a filter" in rep_text
    assert "by construction" in rep_text


def test_the_transfer_limit_and_the_absence_of_causality_are_stated():
    assert "entirely BTC and ETH" in _SRC
    assert "not a sports" in _SRC or "none of it is a sports" in _SRC
    assert "no_causal_claim" in _SRC


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "post(",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_touch_the_frozen_completion_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert frozen not in _SRC, frozen


# ---------------------------------------------------------- against real data
@pytest.mark.skipif(not _have, reason="canonical sequence not present")
def test_time_available_is_the_dominant_predictor_on_real_history():
    rep = rr.report()
    d = rep["predictive_features"]["window_length_minutes"]
    assert d["15"]["completion_rate"] > d["5"]["completion_rate"]
    rem = rep["predictive_features"]["window_remaining_at_first_leg"]
    # monotone in time remaining
    seq = [rem[k]["completion_rate"] for k in sorted(rem)]
    assert all(b >= a for a, b in zip(seq, seq[1:])), seq


@pytest.mark.skipif(not _have, reason="canonical sequence not present")
def test_lopsided_books_pair_worst_on_real_history():
    imb = rr.report()["predictive_features"]["market_imbalance_at_first_leg"]
    assert (imb["balanced 0.35-0.65"]["completion_rate"]
            > imb["lopsided <0.20 or >0.80"]["completion_rate"])


@pytest.mark.skipif(not _have, reason="canonical sequence not present")
def test_the_realized_signal_is_published_stratified_and_mostly_survives():
    rep = rr.report()
    m = rep["does_the_realized_signal_survive"]
    assert m["strata_with_at_least_two_usable_cells"] >= 4
    assert m["share"] >= 0.5
    # and the strata are there to be read, not just summarised
    assert len(rep["realized_stratified_by_window_length_and_time_remaining"]) >= 5
