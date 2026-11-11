"""The extension gate is frozen BEFORE the evidence, or it is worthless."""
from __future__ import annotations

import inspect

import pytest

from src import l5_completion_extension_gate as g


def test_the_sha_matches_what_was_frozen():
    assert g.assert_unmodified() == g.GATE_SHA16_AT_FREEZE


def test_a_tampered_criterion_is_detected(monkeypatch):
    """A threshold that moves after the data arrives is not a threshold.
    The instruction on failure is v2, never an edit to v1."""
    monkeypatch.setitem(g.MUST_IMPROVE, "pairing_rate_relative_gain_min", 0.01)
    with pytest.raises(AssertionError) as e:
        g.assert_unmodified()
    assert "Publish v2" in str(e.value)


def test_the_baseline_is_frozen_not_recomputed():
    """Recomputing a baseline later, from a window overlapping the change,
    is the most comfortable way to prove yourself right."""
    assert g.BASELINE["pairing_rate"] == 0.2011
    assert g.BASELINE["sessions_matched"] == 36
    assert g.BASELINE["sessions_residual_hold"] == 143
    assert "pre-extension" in g.BASELINE["source"]


def test_the_bar_is_derived_from_the_baseline_not_restated():
    """A literal target could drift away from the baseline it came from."""
    assert g.required_pairing_rate() == 0.2413
    assert g.required_pairing_rate() > g.BASELINE["pairing_rate"]


def test_the_money_statement_is_required_not_optional():
    """Stranded residual is where every measured loss is. If it does not
    fall, the extension has not done its job whatever else improved."""
    assert g.MUST_IMPROVE[
        "stranded_residual_usd_per_settled_market_must_fall"] is True


def test_the_rejection_risk_is_a_hard_bound():
    """The risk the GO's framing did not cover: more completion attempts
    must not become more venue refusals, because a rejected complete at a
    non-crossing price revokes the complement proof."""
    assert g.MUST_NOT_WORSEN["rejected_completes_per_session_relative_max"] \
        == 1.25
    assert g.MUST_NOT_WORSEN[
        "complement_revocations_per_100_completes_must_not_rise"] is True


def test_fidelity_is_defended_at_the_same_bar_as_concurrency():
    """This is a clone. Buying pairing by chasing is not a win."""
    from src import l5_concurrency_gate as cg
    assert g.MUST_NOT_WORSEN["acted_relation_mix_l1_worse_by_at_most"] == 0.02
    assert "0.02" in str(cg.CONCURRENCY_GATE)


def test_not_enough_evidence_is_its_own_verdict():
    """Unknown is not the same as failing, and 'safe but pointless' is not
    the same as either."""
    for v in ("KEEP", "REVERT", "NO_MATERIAL_IMPROVEMENT",
              "UNVERIFIABLE", "NOT_YET_ENOUGH_EVIDENCE"):
        assert v in g.VERDICTS


def test_rollback_is_asymmetric():
    assert "NO owner decision" in g.ROLLBACK
    assert "KEEPING it past the trial is an owner decision" in g.ROLLBACK


def test_it_cannot_compute_its_own_criteria():
    """The evaluator imports this; this never imports the evaluator. A gate
    that can measure is a gate that can be argued with."""
    src = inspect.getsource(g)
    # check IMPORTS and CALLS, not prose: BASELINE["source"] legitimately
    # NAMES the module the numbers came from, and matching that string was
    # my own test failing on a provenance label rather than on behaviour
    imports = [l for l in src.splitlines()
               if l.startswith(("import ", "from ")) or " import " in l]
    for line in imports:
        for forbidden in ("l5_completion_economics", "l5_rejection_forensics",
                          "l5_r5f_cohort", "pmus_"):
            assert forbidden not in line, line
    for call in ("all_orders(", "_messages(", "open(", "requests.",
                 "place_limit_buy(", "run_campaign("):
        assert call not in src, call
