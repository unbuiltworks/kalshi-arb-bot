"""The counterfactual is only allowed to report what the tape can support.

The load-bearing property is that c>=2 comes back UNVERIFIABLE rather than
as an estimate: the cycles a second slot would have used are exactly the
cycles with no recorded candidate universe, so any number there would be an
assumption about the thing under test."""
from __future__ import annotations

from pathlib import Path

from src import l5_concurrency_counterfactual as cf
from src import l5_concurrency_gate as gate


def test_the_frozen_gate_is_checked_before_anything_is_computed():
    src = Path(cf.__file__).read_text(encoding="utf-8")
    body = src.split("def build_report")[1]
    before = body.split("assert_unmodified")[0]
    # nothing but the imports/log expansion may precede the gate check
    assert "arm_c1" not in before and "selection_timeline" not in before


def test_no_threshold_is_restated_inline():
    """A threshold copied into the scorer can drift from the frozen one."""
    src = Path(cf.__file__).read_text(encoding="utf-8")
    for frozen_number in ("72", "40", "20", "0.15", "2.0x", "0.02"):
        assert f"= {frozen_number}" not in src
    assert "gate.CONCURRENCY_GATE" in src


def test_higher_arms_are_unverifiable_and_say_why():
    missing = {"cycles_held_with_no_ranked_universe": 284,
               "cycles_with_a_ranked_universe": 93}
    a = cf.arm_unverifiable(2, missing)
    assert a["verdict"] == "UNVERIFIABLE"
    assert a["observed"] is False
    assert a["cycles_that_cannot_be_reconstructed"] == 284
    assert "assumption about the thing under test" in a["refused_to_estimate"]


def test_the_hole_is_measured_not_asserted():
    sel = [{"t": 0.0, "chosen": "a", "n_admitted": 3, "admitted": []},
           {"t": 20.0, "chosen": None, "n_admitted": 0, "admitted": []}]
    skipped = [{"t": 5.0, "reason": "CAMPAIGN_HOLD:POSITION_HELD"}] * 3
    m = cf.missing_evidence_for_higher_arms(sel, skipped)
    assert m["cycles_with_a_ranked_universe"] == 1
    assert m["cycles_held_with_no_ranked_universe"] == 3
    assert m["share_of_cycles_unreconstructible"] == 0.75


def test_the_fix_it_names_is_log_only_and_needs_a_go():
    m = cf.missing_evidence_for_higher_arms([], [])
    fix = m["what_would_fix_it"]
    assert "log-only" in fix and "no order path" in fix
    assert "needs an owner go" in fix


def test_it_enacts_nothing():
    """Checks CALL syntax, not bare mentions: the module's docstring names
    run_campaign when explaining why the c>=2 arms are unverifiable, and a
    substring test would fail on its own prose."""
    src = Path(cf.__file__).read_text(encoding="utf-8")
    for forbidden in ("place_limit_buy(", "run_campaign(", "run_session(",
                      "os.environ[", "cancel_exact(", "save_complement_proof("):
        assert forbidden not in src, forbidden


def test_unmet_minimums_win_over_everything_else():
    """A gate cannot be passed OR failed on a sample that does not meet its
    own minimums."""
    c1 = {"completed_sequences": 1, "settled_markets_venue_confirmed": 0}
    arms = {"c2": {"verdict": "UNVERIFIABLE"}}
    out = cf.score_against_gate(c1, arms, {}, [{"t": 0.0, "chosen": "a"},
                                               {"t": 10.0, "chosen": "b"}])
    assert out["verdict"] == "NOT_YET_ENOUGH_FORWARD_EVIDENCE"
    assert out["gate_sha256_16"] == gate.GATE_SHA16_AT_FREEZE
