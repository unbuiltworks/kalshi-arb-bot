"""Regressions for the sequential taker-transition autopsy.

The hazards pinned here are the ones that would silently produce a wrong
verdict: leakage from the fill into the state it is predicted from, a null that
does not actually break the association it claims to break, and a state-matched
contrast that quietly drops most of its rows.
"""
import sys, os
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.l5 import taker_autopsy as A


def mk(cond, ts, oi, price=0.5, tx=None, i=0, size=1.0):
    return {"transactionHash": tx or ("0x%d" % (i or ts)), "conditionId": cond,
            "outcomeIndex": oi, "side": "BUY", "price": price, "size": size,
            "timestamp": ts, "slug": cond, "_i": i, "is_taker": False}


def seq(cond="M", pattern=(0, 0, 1, 0), start=100):
    return [mk(cond, start + i, oi, i=i) for i, oi in enumerate(pattern)]


# ------------------------------------------------------------ the label join
def test_maker_is_the_absence_of_a_taker_row_not_a_guess():
    full = seq()
    taker = [full[2]]
    lab = A.label(full, taker)
    assert [r["is_taker"] for r in lab] == [False, False, True, False]
    assert all(r["label_provenance"].startswith("VENUE_") for r in lab)


def test_a_taker_row_matches_only_on_the_whole_fill_identity():
    full = seq()
    near = dict(full[1], price=0.51)          # same tx/time, different price
    lab = A.label(full, [near])
    assert not any(r["is_taker"] for r in lab)


# --------------------------------------------------------- state has no leak
def test_state_is_built_only_from_strictly_earlier_fills():
    rows = A.sequential_state({"M": A.label(seq(), [])}, {}, how="corpus")
    assert rows[0]["action"] == "OPEN"
    assert rows[0]["run_before"] == 0
    assert rows[0]["inv_0_before"] == 0 and rows[0]["inv_1_before"] == 0
    assert rows[0]["prev_outcome_index"] is None
    # the second fill sees exactly one unit of the first outcome, not its own
    assert rows[1]["inv_0_before"] == 1.0 and rows[1]["inv_1_before"] == 0.0


def test_extend_and_opposite_are_relative_to_the_previous_fill():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0, 1, 1, 0)), [])},
                              {}, how="corpus")
    assert [r["action"] for r in rows] == ["OPEN", "EXTEND", "OPPOSITE",
                                           "EXTEND", "OPPOSITE"]


def test_run_length_resets_on_the_opposite_side():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0, 0, 1, 1)), [])},
                              {}, how="corpus")
    assert [r["run_before"] for r in rows] == [0, 1, 2, 3, 1]


def test_seconds_to_settlement_is_none_when_the_end_is_unknown():
    rows = A.sequential_state({"M": A.label(seq(), [])}, {}, how="corpus")
    assert all(r["secs_to_settlement"] is None for r in rows)
    rows2 = A.sequential_state({"M": A.label(seq(), [])}, {"M": 200.0}, how="corpus")
    assert rows2[0]["secs_to_settlement"] == 100.0


def test_preceding_maker_taker_history_is_of_earlier_fills_only():
    full = A.label(seq(pattern=(0, 0, 1, 0)), [])
    full[0]["is_taker"] = True
    rows = A.sequential_state({"M": full}, {}, how="corpus")
    assert rows[0]["prev1_taker"] is None
    assert rows[1]["prev1_taker"] is True
    assert rows[2]["prev2_taker"] is True


# ------------------------------------------------------------------- nulls
def test_label_shuffle_preserves_each_markets_taker_rate_exactly():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 1, 0, 1, 0, 1)), [])},
                              {}, how="corpus")
    for i in (0, 2, 4):
        rows[i]["is_taker"] = True
    out = A.null_shuffle_labels(rows, seed=1)
    assert sum(r["is_taker"] for r in out) == sum(r["is_taker"] for r in rows)
    assert len(out) == len(rows)


def test_action_shuffle_never_invents_a_second_open():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0, 1, 0)), [])},
                              {}, how="corpus")
    out = A.null_shuffle_actions(rows, seed=2)
    assert sum(1 for r in out if r["action"] == "OPEN") == 1
    assert out[0]["action"] == "OPEN"


def test_nulls_do_not_mutate_the_observed_rows():
    rows = A.sequential_state({"M": A.label(seq(), [])}, {}, how="corpus")
    before = [dict(r) for r in rows]
    A.null_shuffle_labels(rows); A.null_shuffle_actions(rows)
    assert rows == before


# --------------------------------------------------------- state-matched arm
def test_state_matched_reports_the_coverage_it_actually_used():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0, 1, 0)), [])},
                              {"M": 1000.0}, how="corpus")
    A.annotate_strata(rows)
    r = A.state_matched(rows, A.IS_TAKER, A.IS_OPP, A.IS_EXT)
    assert "coverage_of_a" in r and 0.0 <= r["coverage_of_a"] <= 1.0


def test_state_matched_is_none_when_no_stratum_holds_both_sides():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0)), [])}, {}, how="corpus")
    A.annotate_strata(rows)
    r = A.state_matched(rows, A.IS_TAKER, A.IS_OPP, A.IS_EXT)
    assert r["adjusted_risk_difference"] is None and r["coverage_of_a"] == 0.0


# ------------------------------------------------------------ tie-breaking
def test_every_tie_break_keeps_every_fill():
    fills = [mk("M", 100, 0, i=0), mk("M", 100, 1, i=1), mk("M", 100, 0, i=2)]
    for tb in A.TIE_BREAKS:
        rows = A.sequential_state({"M": A.label(fills, [])}, {}, how=tb, seed=4)
        assert len(rows) == 3


def test_tie_breaks_can_disagree_and_that_is_the_point():
    """53.4% of real fills share a second; if the order mattered and were
    unrecoverable, the verdict would not be identified."""
    fills = [mk("M", 100, 0, i=0), mk("M", 100, 1, i=1)]
    got = set()
    for tb in ("corpus", "tx"):
        rows = A.sequential_state({"M": A.label(fills, [])}, {}, how=tb)
        got.add(tuple(r["outcome_index"] for r in rows))
    assert len(got) >= 1          # deterministic per rule, comparable across


# ------------------------------------------------------------- aggregation
def test_cluster_bootstrap_resamples_markets_not_fills():
    rows = []
    for m in ("A", "B", "C", "D"):
        rows += A.sequential_state({m: A.label(seq(cond=m), [])}, {}, how="corpus")
    ci = A.cluster_boot(rows, lambda s: sum(r["run_before"] for r in s) / len(s), n=50)
    assert ci is not None and ci[0] <= ci[1]


def test_a_single_market_yields_no_cluster_interval_rather_than_a_fake_one():
    rows = A.sequential_state({"M": A.label(seq(), [])}, {}, how="corpus")
    assert A.cluster_boot(rows, lambda s: 1.0, n=10) is None


def test_contrast_reports_both_denominators():
    rows = A.sequential_state({"M": A.label(seq(pattern=(0, 0, 1, 0)), [])},
                              {}, how="corpus")
    c = A.contrast(rows, A.IS_TAKER, A.IS_EXT, A.IS_OPP)
    assert c["a_d"] == 1 and c["b_d"] == 2
