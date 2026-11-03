"""Grok's inventory-aware entry policy, evaluated and refused.

THE PROPOSAL: (1) a time-decaying residual inventory budget, (2) an
adverse-selection-adjusted entry threshold, (3) the existing profitable
pair-completion logic, preserved.

WHAT THE REPLAY FOUND, and why it is a NO-GO:

  * Component 1 has NO MEASURABLE EFFECT. The arm with the decaying budget
    alone is byte-identical to the baseline -- 44 BUDGET refusals that changed
    not one outcome, because with a 2-contract clip and a 5-contract cap the
    budget only binds after T+360, where the price band had already refused.
  * Component 2 at full calibration REFUSES EVERY TRADE. 294 EV_NEGATIVE
    refusals, zero orders, net exactly 0.0000. Its "improvement" over a
    losing baseline is refusal, not edge -- which is the failure mode the
    owner asked to be reported explicitly.
  * No arm improves BOTH chronological halves. Every arm that improves the
    holdout worsens the train half.
  * Component 3 was already working and was not touched.

AND IT IS NOT L5. The policy permits 5.7% of L5's 63,686 actions and 0.5% of
his openings. Our adverse-selection penalty is a property of OUR execution --
2 contracts resting 2 ticks behind a 52,000-100,000 share touch -- not of the
market, so applying it to a size-scale liquidity provider measures a category
error rather than his strategy.

These tests pin the arithmetic and the verdict so neither can drift, and so a
later reader cannot mistake the refusal for an edge.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

DOC = json.load(open("analysis/l5/grok_inventory_policy_20261103.json"))
CAL = DOC["penalty_calibration"]
ARMS = {a["arm"]: a for a in DOC["arms"]}
EDGE = D("0.02")


# ------------------------------------------------- the penalty arithmetic

def test_the_penalty_is_calibrated_strictly_before_the_replay_window():
    """Temporal separation is the whole reason the number means anything."""
    assert CAL["calibration_legs"] == 30
    assert CAL["held_out_legs"] == 14
    assert "BEFORE the tape begins" in CAL["method"]


def test_ev_per_unit_is_edge_minus_penalty_in_every_band():
    for band, pen in CAL["by_band"].items():
        if band == "POOLED":
            continue
        got = D(str(CAL["ev_per_unit_at_full_penalty"][band]))
        want = EDGE - D(str(pen))
        assert got == want, band


def test_only_the_sub_0_20_band_has_positive_risk_adjusted_ev():
    pos = [b for b, v in CAL["ev_per_unit_at_full_penalty"].items() if v > 0]
    assert pos == ["0.00-0.20"]


def test_every_band_the_engine_actually_trades_is_negative():
    """Proved separately: at depth 2 on a 1-tick book the chosen opening own
    price is always in [0.20, 0.47]. Those bands are 0.20-0.35 and 0.35-0.50."""
    for band in ("0.20-0.35", "0.35-0.50"):
        assert CAL["ev_per_unit_at_full_penalty"][band] < 0, band


# ------------------------------------------------- component 1 does nothing

def test_the_time_decaying_budget_alone_changes_nothing():
    base = ARMS["BASELINE existing executor"]
    g1 = ARMS["G1 time-decaying budget ONLY"]
    for k in ("net", "train", "holdout", "pairs", "stranded", "traded_pct"):
        assert g1[k] == base[k], k
    assert "NO measurable effect" in g1["verdict"]


# ------------------------------------------------- component 2 refuses all

@pytest.mark.parametrize("arm", ["G4 penalty lam=0.50",
                                 "G5 penalty lam=1.00",
                                 "G6 FULL Grok policy"])
def test_at_full_calibration_the_policy_refuses_every_trade(arm):
    a = ARMS[arm]
    assert a["traded_pct"] == 0
    assert a["pairs"] == 0
    assert a["net"] == 0.0
    assert "REFUSES EVERY TRADE" in a["verdict"]


def test_the_refusal_is_disclosed_rather_than_presented_as_an_improvement():
    d = DOC["refusal_disclosure"]
    assert "REFUSAL, not edge" in d
    assert "100% of trades" in d


def test_no_arm_improves_both_chronological_halves():
    base = ARMS["BASELINE existing executor"]
    bt, bh = base["train"], base["holdout"]
    both = [a["arm"] for a in DOC["arms"]
            if a.get("train") is not None and a.get("holdout") is not None
            and a["train"] > bt and a["holdout"] > bh
            and a["traded_pct"] > 0]
    assert both == [], both


# ------------------------------------------------- the penalty does do work

def test_opening_the_band_floor_without_the_penalty_is_worse_than_baseline():
    """So the penalty is not inert in G7 -- it filters. It just cannot filter
    its way to a policy that beats the baseline on both halves."""
    base = ARMS["BASELINE existing executor"]
    g9 = ARMS["G9 band floor 0.05 ONLY"]
    assert g9["traded_pct"] == 100
    assert g9["net"] < base["net"]


def test_the_one_trading_arm_that_beats_the_holdout_rests_on_five_legs():
    g7 = ARMS["G7 lam=1 + decay + floor 0.05"]
    base = ARMS["BASELINE existing executor"]
    assert g7["holdout"] > base["holdout"]        # better holdout
    assert g7["train"] < base["train"]            # worse train
    assert "5 calibration legs" in g7["verdict"]


# ------------------------------------------------- L5 fidelity

def test_the_policy_does_not_explain_L5():
    f = DOC["l5_fidelity"]
    assert f["actions"] == 63686
    assert f["permitted_pct"]["ALL"] == 5.7
    assert f["permitted_pct"]["OPEN"] == 0.5
    assert "does NOT explain L5" in f["verdict"]


def test_the_budget_was_scaled_to_L5_and_not_to_our_capital():
    """Testing our $12 cap against a trader whose p95 residual is 427
    contracts would have measured scale, not policy."""
    assert "427" in DOC["l5_fidelity"]["cap_used"]


def test_L5_trades_heavily_where_our_penalty_says_EV_is_worst():
    d = DOC["l5_fidelity"]["l5_own_px_distribution"]
    assert d["0.65-1.01"] > 25
    assert CAL["ev_per_unit_at_full_penalty"]["0.65-1.01"] < -0.5
    assert "category error" in DOC["l5_fidelity"]["why"]


# ------------------------------------------------- the existing-controls gap

def test_the_gap_is_real_and_not_a_duplicate_safeguard():
    g = DOC["existing_controls_gap"]
    assert "NO clock parameter" in g["quantity_only_gate"]
    assert "NO expected-value parameter" in g["quantity_only_gate"]
    assert "ZERO call sites" in g["dead_ev_machinery"]
    assert "not a duplicate" in g["conclusion"]


def test_the_dead_ev_machinery_really_has_no_call_sites():
    """Asserted against the source, not just the write-up."""
    import pathlib
    src = pathlib.Path("src")
    hits = []
    for p in src.glob("*.py"):
        for ln in p.read_text().splitlines():
            if "expected_value(" in ln and "def expected_value" not in ln:
                hits.append("%s:%s" % (p.name, ln.strip()[:60]))
    assert hits == [], hits


def test_the_refuse_gate_still_has_no_clock_and_no_ev_input():
    import inspect
    from src import l5_live_validation_gates as G
    ps = set(inspect.signature(G.ValidationGates.refuse).parameters)
    assert not {"now", "t_left", "tte", "elapsed", "runway", "deadline"} & ps
    assert not {"ev", "expected_value", "edge", "residual_alpha"} & ps


# ------------------------------------------------- what was not invented

def test_the_maker_fee_by_effective_date_is_reported_impossible_not_guessed():
    m = DOC["fee_schedule"]["maker_by_effective_date"]
    assert m.startswith("NOT POSSIBLE")
    assert "no maker field" in m
    assert "Missing input named" in m


def test_the_taker_correction_is_the_part_that_was_verifiable():
    assert "0.0695" in DOC["fee_schedule"]["taker"]
    assert "NOT corroborated" in DOC["fee_schedule"]["taker"]


def test_the_cancellation_margin_is_flagged_as_not_proven():
    c = DOC["cancellation_margin"]
    assert "NOT DEPLOYED" in c["status"]
    assert "TWO observations" in c["what_is_NOT_proven"]
    assert "should not be deployed" in c["what_is_NOT_proven"]


def test_the_verdict_is_no_go():
    assert DOC["verdict"] == "NO-GO"
    assert DOC["basis"].startswith("REPLAY")
    assert "Incentives counted at ZERO" in DOC["basis"]
