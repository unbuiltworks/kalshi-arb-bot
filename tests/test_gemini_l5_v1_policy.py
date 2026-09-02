"""Tests for the validated-v1 L5 policy gate (decision filter only)."""
from __future__ import annotations

from decimal import Decimal as D

from src import gemini_l5_v1_policy as v1


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(v1.POLICY_ENV, raising=False)
    assert v1.enabled() is False
    monkeypatch.setenv(v1.POLICY_ENV, "1")
    assert v1.enabled() is True


def test_gate_file_provenance_pins_frozen_model():
    prov = v1.provenance()
    assert prov["model_sha256_16"] == "a846e6bc96fc8079"
    assert prov["training_since_epoch"] == 1774569600     # Mar 27 regime
    assert prov["n_decisions"] == 69646


def test_state_key_matches_v1_vocabulary():
    assert v1.state_key(0.50, 200.0, 0.0) == "p45-55c|tte2-5m|flat"
    assert v1.state_key(0.93, 12.0, 60.0) == "p>90c|tte<30s|imb>50"


def test_evaluate_in_distribution_allows_with_provenance():
    verdict = v1.evaluate(0.50, 200.0, 0.0)
    assert verdict["allow"] is True
    assert verdict["model_sha256_16"] == "a846e6bc96fc8079"
    assert verdict["modal_relation"] in {"open", "complete", "extend"}
    assert abs(sum(verdict["rel_probs"].values()) - 1.0) < 0.01


def test_evaluate_off_distribution_stands_down():
    # a state L5 never visited (found by scanning the gate for a hole)
    gate = v1.load_gate()
    hole = None
    for p, tte, r in ((0.02, 5.0, 0.0), (0.98, 5.0, 0.0), (0.02, 4000.0, 0.0)):
        if v1.state_key(p, tte, r) not in gate["states"]:
            hole = (p, tte, r)
            break
    assert hole is not None, "expected at least one unvisited coarse state"
    verdict = v1.evaluate(*hole)
    assert verdict["allow"] is False
    assert verdict["reason"] == "off_distribution"


def test_gate_candidate_uses_mid():
    verdict = v1.gate_candidate(D("0.48"), D("0.52"), 200.0)
    assert verdict["state"].startswith("p45-55c|")
