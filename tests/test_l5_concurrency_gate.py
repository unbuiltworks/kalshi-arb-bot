"""The concurrency gate is pre-registration, so the tests are about the
ordering and the tamper-evidence, not about any number it produces."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import l5_concurrency_gate as gate


def test_the_frozen_sha_matches_the_frozen_criteria():
    assert gate.gate_sha16() == gate.GATE_SHA16_AT_FREEZE
    gate.assert_unmodified()


def test_editing_a_criterion_is_detected():
    """The whole value of pre-registration is that a later edit cannot pass
    silently."""
    tampered = json.loads(json.dumps(gate.CONCURRENCY_GATE))
    tampered["sample_minimums"]["completed_sequences_at_c1"] = 5
    assert gate.gate_sha16(tampered) != gate.GATE_SHA16_AT_FREEZE


def test_the_gate_enacts_nothing():
    src = Path(gate.__file__).read_text(encoding="utf-8")
    # no execution surface of any kind lives in a pre-registration module
    for forbidden in ("place_limit_buy", "run_session", "run_campaign",
                      "set-variables", "MAX_CONCURRENCY", "os.environ["):
        assert forbidden not in src, forbidden
    assert "NOTHING" in gate.CONCURRENCY_GATE["enacts"]


def test_it_cannot_buy_throughput_with_fidelity_or_with_worst_case_exposure():
    """Both directions must be pinned: the gate exists to stop 'more trades'
    being mistaken for 'better'."""
    must_improve = gate.CONCURRENCY_GATE["must_improve"]
    must_not = gate.CONCURRENCY_GATE["must_not_worsen"]
    assert "l5_relation_fidelity" in must_improve
    assert "completed_sequences_per_bankroll_dollar_hour" in must_improve
    assert "worst_simultaneous_unmatched_usd" in must_not
    assert "residual_loss_per_completed_sequence" in must_not


def test_money_is_settled_and_incentives_stay_separate():
    rules = gate.CONCURRENCY_GATE["accounting_rules"]
    assert "settled" in rules["realized_only"]
    assert "No mark" in rules["realized_only"]
    assert "SEPARATELY" in rules["incentives"]


def test_rollback_needs_no_new_owner_decision_but_the_increase_does():
    rb = gate.CONCURRENCY_GATE["rollback"]
    assert "does not require a new owner decision" in rb["action"]
    assert "only the increase does" in rb["action"]


def test_unverifiable_is_a_distinct_verdict_from_do_not_recommend():
    """Quantities that cannot be tied to venue evidence are unknown, not a
    failing score -- the same rule the fill reconciliation uses."""
    assert "UNVERIFIABLE" in gate.CONCURRENCY_GATE["verdict_values"]
    assert "DO_NOT_RECOMMEND" in gate.CONCURRENCY_GATE["verdict_values"]
