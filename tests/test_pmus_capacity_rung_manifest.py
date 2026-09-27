from decimal import Decimal as D

from src import pmus_capacity_rung_manifest as M


def test_envelope_lineage_is_separate_and_reserved():
    assert M.PROBE_CAP_02_USD == D("5.00")
    assert M.allocable_usd() == D("4.00")          # $1.00 held back
    assert M.per_market_worst_case_cap() == D("2.00")


def test_unmatched_leg_loses_exactly_its_basis():
    assert M.worst_case_realized(D(10), D("0.20")) == D("2.0000")
    assert M.worst_case_realized(D(50), D("0.45")) == D("22.5000")


def test_deployed_capital_is_not_loss():
    # a completed pair deploys ~q*0.96 and pays q*1.00; that is capital
    assert M.deployed_capital_for_pair(D(10)) == D("9.6000")


def test_rung_a_is_admitted_at_the_proposed_bound():
    r = M.evaluate_rung(name="A", q=D(10), entry_price_max=D("0.20"),
                        max_unmatched_usd=D("2.00"),
                        rung_loss_budget_usd=D("1.50"))
    assert r["verdict"] == "ADMIT", r["refusals"]
    assert r["is_modeled_extrapolation"] is True
    assert r["clip_vs_demonstrated_live"] == "1.88x"


def test_mid_band_q10_is_refused_because_one_market_would_eat_the_envelope():
    r = M.evaluate_rung(name="A-midband", q=D(10), entry_price_max=D("0.45"),
                        max_unmatched_usd=D("2.00"),
                        rung_loss_budget_usd=D("1.50"))
    assert r["verdict"] == "REFUSE"
    assert any("WORST_CASE_EXCEEDS_PER_MARKET_CAP" in x for x in r["refusals"])


def test_q25_and_q50_refused_at_spring_band():
    for q in (D(25), D(50)):
        r = M.evaluate_rung(name="x", q=q, entry_price_max=D("0.45"),
                            max_unmatched_usd=D("2.00"),
                            rung_loss_budget_usd=D("1.00"))
        assert r["verdict"] == "REFUSE"


def test_max_clip_for_band_matches_the_frontier():
    assert M.max_clip_for_band(D("0.20")) == D("10.00")
    assert M.max_clip_for_band(D("0.50")) == D("4.00")


def test_preflight_fails_closed_on_missing_authenticated_state():
    out = M.preflight({})
    assert out["armed"] is False
    assert out["verdict"] == "DISARMED_REFUSE"
    assert "credentials_present" in out["unsatisfied"]
    assert "no_open_orders" in out["unsatisfied"]


def test_preflight_clear_still_cannot_arm():
    out = M.preflight({k: True for k in (
        "balances_verified", "positions_flat_in_target_market",
        "no_open_orders", "credentials_present", "arm_trio_valid",
        "gate_evidence_matches_slug", "artifact_commit_is_current_head")})
    assert out["verdict"] == "PREFLIGHT_CLEAR"
    assert out["armed"] is False
