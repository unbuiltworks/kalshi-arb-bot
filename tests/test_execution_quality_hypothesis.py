"""The execution-quality hypothesis, tested and refuted.

THE HYPOTHESIS (owner, offered explicitly as a hypothesis): L5's inventory is
roughly fairly priced while our comparable fills are severely adversely
selected, so execution quality may be the largest difference.

THE TEST. Compute L5's residual P&L per unit on EXACTLY our measure -- the
cost of the excess side at its average own price, against what that side
redeemed at settlement -- so the two numbers condition on the same thing.

    L5    104,961.8 units   paid 0.3504/u   redeemed 0.1637/u   net -0.1867/u
    ours                                                        net -0.1855/u

A difference of 0.0012 a unit. L5 is adversely selected on residual to the
same degree we are, so execution quality is NOT the difference.

WHY I GOT THIS WRONG ONE TURN EARLIER, recorded because the error is
instructive: I compared his alpha on ALL FILLS (+0.0043, z 0.8, in the
0.35-0.50 band) against our realized loss on STRANDED LEGS (-0.3994/unit).
Those are different conditioning sets -- the exact category error I had
warned about a turn before that. Matched properly, the measures agree.

WHAT THE DIFFERENCE ACTUALLY IS. Residual costs both of us about 0.186 a
unit. L5 earns matched profit on 5.30 contracts for every residual unit; we
earn it on 1.786, against a breakeven of about 2.012. He clears it on
COMPLETION RATE, and his reported edge sits in incentives -- reconstructed
trading P&L -$2,829 against roughly $71,500 credited. He was paid to carry
the residual; he did not out-execute us on it.
"""
from __future__ import annotations

import json

import pytest

DOC = json.load(open("analysis/l5/execution_quality_hypothesis_20261103.json"))
D8 = DOC["item8_decomposition"]
D7 = DOC["item7_sensitivity"]


# ------------------------------------------------- the refutation

def test_the_hypothesis_is_refuted_on_the_matched_measure():
    assert "REFUTED" in DOC["headline"]
    assert abs(D8["l5"]["net_per_unit"] - D8["ours"]["net_per_unit"]) < 0.005
    assert D8["difference_per_unit"] < 0.005


def test_both_sides_lose_about_the_same_on_residual():
    assert D8["l5"]["net_per_unit"] < 0
    assert D8["ours"]["net_per_unit"] < 0
    assert D8["verdict"].startswith("statistically indistinguishable")


def test_l5_residual_arithmetic_is_internally_consistent():
    l = D8["l5"]
    assert abs((l["redeemed_per_unit"] - l["paid_per_unit"]) - l["net_per_unit"]) < 1e-9


def test_the_prior_framing_is_recorded_as_wrong():
    """The correction is kept in the artifact so it cannot be re-derived."""
    w = D8["my_prior_framing_was_WRONG"]
    assert "ALL FILLS" in w and "STRANDED LEGS" in w
    assert "category error" in w


def test_the_band_where_our_residual_sits_is_the_same_order_of_magnitude():
    b = D8["residual_by_band"]["0.35-0.50"]
    assert b["l5_net_per_unit"] < 0 and b["ours"] < 0
    assert abs(b["ours"] / b["l5_net_per_unit"]) < 2.0     # not a 100x gap


# ------------------------------------------------- what the difference is

def test_the_difference_is_the_matched_to_residual_ratio():
    d = D8["what_the_difference_actually_IS"]
    assert d["l5_ratio"] > d["breakeven_ratio"] > d["our_ratio"]
    assert abs(d["l5_matched_contracts"] / d["l5_residual_units"]
               - d["l5_ratio"]) < 0.01


def test_l5_is_overwhelmingly_a_maker():
    assert D8["order_placement"]["l5_maker_pct"] > 85


def test_his_maker_alpha_is_monotone_in_price_not_uniformly_good():
    a = D8["order_placement"]["maker_alpha_by_band"]
    vals = [a[k] for k in ("0.00-0.20", "0.20-0.35", "0.35-0.50",
                           "0.50-0.65", "0.65-1.01")]
    assert vals == sorted(vals)          # monotone increasing
    assert vals[0] < 0 < vals[-1]        # adverse cheap, favourable dear


def test_queue_position_is_reported_unmeasurable_on_both_sides():
    q = D8["queue_position"]
    assert "NOT MEASURABLE" in q
    assert "our_position_within_level UNKNOWN" in q


def test_the_venue_difference_is_named_as_incentives():
    v = D8["venue_differences"]
    assert "-$2,829" in v and "71,500" in v
    assert "paid to carry it" in v


# ------------------------------------------------- sensitivity

def test_aggressive_repricing_beats_baseline_at_every_fill_probability():
    base = DOC["item7_sensitivity"]["baseline_A"]["holdout"]
    for row in D7["p_fill_sweep"]:
        assert row["beats_A"] is True, row["p_fill"]
        assert row["holdout"] > base, row["p_fill"]


def test_the_apparent_train_gain_at_low_fill_probability_is_flagged():
    """At p_fill 0.25 train turns positive while pairs fall -- the refusal
    pattern, and it is labelled as such rather than counted as edge."""
    low = [r for r in D7["p_fill_sweep"] if r["p_fill"] == 0.25][0]
    assert low["train"] > 0
    assert low["pairs"] < D7["p_fill_sweep"][0]["pairs"]
    assert "refusal pattern" in D7["reading"]


def test_the_latency_knob_is_reported_inert_not_harmless():
    r = D7["latency_knob_was_INERT_and_here_is_why"]
    assert "byte-identical" in r
    assert "SUB-TICK" in r
    assert "not harmless" in r


# ------------------------------------------------- the deliverable

def test_no_money_path_change_was_made_this_turn():
    assert DOC["deliverable"]["smallest_safe_change"].startswith("NONE")


def test_the_099_cap_remains_refused_on_oos_evidence():
    w = DOC["deliverable"]["why_not_the_099_cap"]
    assert "-2.83" in w          # the holdout it produces
    assert "single observation" in w


def test_the_missing_measurements_are_named_rather_than_modelled():
    m = DOC["deliverable"]["minimum_additional_real_time_measurements"]
    assert len(m) >= 4
    assert any("queue position" in x for x in m)
    assert any("L5_COMPLETION_REQUOTE" in x for x in m)


def test_safety_is_unchanged():
    s = DOC["safety"]
    assert s["cancel_margin"].startswith("300s")
    assert s["armed"] is False
    assert s["live_orders"] == 0
