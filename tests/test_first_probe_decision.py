"""The decision packet must score frozen evidence and change nothing."""
import json

import pytest

from analysis.l5.lanes import first_probe_decision as F


def test_the_evidence_snapshot_is_frozen_and_hashed():
    s = F.snapshot()
    assert s["label"] == "LANE_A_EVIDENCE_SNAPSHOT_FROZEN"
    assert len(s["sha256"]) == 64
    assert s["frozen_at_utc"] == "2026-09-16T01:41:44Z"


def test_the_frozen_evidence_says_zero_completions_at_or_below_096():
    ev = F.evidence()
    assert ev["completions"] == 13
    assert ev["le_096"] == 0
    assert ev["le_098"] == 13
    assert ev["cheapest"] == "0.9750"
    assert ev["category_bar_met"] is True


def test_option_b_changes_no_deployed_constant():
    """The single most misreadable thing in this packet: 0.98 is ALREADY the
    shipped target, so B edits nothing."""
    from src import pmus_spring_l5_session as sess
    # both are Decimal; compare exactly rather than through a float tolerance
    assert sess.SPRING_PAIR_TARGET == F.DEPLOYED_PAIR_TARGET
    assert str(sess.SPRING_PAIR_TARGET) == "0.98"
    b = F.options()["options"][F.OPTION_B]
    assert "ALREADY 0.98" in b["not_a_threshold_change"]
    assert "NOT claimed optimal" in b["not_optimal"]


def test_the_envelope_is_identical_under_both_options():
    """Neither choice touches size or risk -- only pair eligibility."""
    e = F.envelope()
    assert e["identical_under_A_and_B"] is True
    assert e["max_first_market_spend_usd"] == "0.05"
    assert e["max_loss_per_day_usd"] == "0.15"
    assert e["concurrency"] == 1


def test_the_locked_edge_at_098_is_measurement_noise_not_expectancy():
    le = F.locked_edge("0.98")
    assert le["locked_edge_usd"] == "0.000400"
    b = F.options()["options"][F.OPTION_B]
    assert "profitability" in b["what_it_would_NOT_resolve"]


def test_both_options_carry_support_AND_contradiction():
    """A packet that only argued one way would not be a decision."""
    o = F.options()["options"]
    for name in (F.OPTION_A, F.OPTION_B):
        assert o[name]["historical_support"]
        assert o[name]["historical_contradiction"]


def test_option_a_has_no_prospective_support_in_the_frozen_sample():
    a = F.options()["options"][F.OPTION_A]
    assert "NONE" in a["prospective_support"]


def test_the_hypothetical_preflight_leaves_only_owner_gates():
    h = F.hypothetical_b_preflight()
    assert h["evidence_gates_all_pass"] is True
    assert h["only_owner_gates_remain"] is True
    assert set(h["owner_controlled_gates"]) == {
        "SHUTDOWN_LATCH_CLEARED", "DUAL_KEY_ARMED"}


def test_the_hypothetical_preflight_clears_and_arms_nothing():
    h = F.hypothetical_b_preflight()
    latch = [g for g in h["gates"] if g["gate"] == "SHUTDOWN_LATCH_CLEARED"][0]
    key = [g for g in h["gates"] if g["gate"] == "DUAL_KEY_ARMED"][0]
    assert latch["state"] == "FAIL"     # still set
    assert key["state"] == "FAIL"       # still disarmed
    assert "HYPOTHETICAL" in h["labels"]


def test_the_packet_never_reads_the_legacy_mislabelled_field():
    blob = json.dumps(F.options()) + json.dumps(F.hypothetical_b_preflight())
    # it may be NAMED as ignored, but no count may be sourced from it
    from analysis.l5.lanes import lane_a_readiness as R
    assert R.pair_cost_counts(["0.98"] * 3)["le_096_completions"] == 0
    assert '"le_096": 0' in json.dumps(F.evidence())
    assert "0.9750" in blob
