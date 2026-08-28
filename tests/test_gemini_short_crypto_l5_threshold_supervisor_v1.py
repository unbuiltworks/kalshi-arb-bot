from decimal import Decimal as D

from src import gemini_short_crypto_l5_threshold_supervisor_v1 as sup
from src.gemini_l5_threshold_model import plan_threshold_quotes


def test_hard_live_limits_are_small():
    assert sup.HARD_MAX_EXECUTED_TOTAL == 2
    assert sup.HARD_MAX_EXECUTED_PER_HORIZON == 1
    assert sup.HARD_MAX_REALIZED_LOSS == D("1.00")
    assert sup.HARD_MAX_RUNTIME_S == 3600


def test_reconciliation_lag_is_pending_but_identity_mismatch_is_not():
    assert sup.settlement_pending(RuntimeError("missing exact canary order history expected=2 found=1"))
    assert sup.settlement_pending(RuntimeError("open reservation has zero authoritative fills; manual review required"))
    assert not sup.settlement_pending(RuntimeError("client-id/outcome mismatch"))


def test_dual_key_arm_requires_exact_session_match(monkeypatch):
    monkeypatch.setenv(sup.LIVE_ENV, "YES")
    monkeypatch.setenv(sup.SESSION_ID_ENV, "abc")
    monkeypatch.setenv(sup.SESSION_ARM_ENV, "wrong")
    assert sup.live_armed() is False
    monkeypatch.setenv(sup.SESSION_ARM_ENV, "abc")
    assert sup.live_armed() is True


def test_modeled_rebate_is_separate_from_trading_pnl():
    plan = plan_threshold_quotes(D("0.60"), D("0.65"))
    result = {"yes_fill": "1", "no_fill": "1", "net_profit": "0.02"}
    rebate = sup._modeled_rebate_for_result(plan, result)
    assert D(rebate["raw_total"]) > D("0")
    # q=1 rebates are sub-cent here, so the conservative cent-rounded model is zero.
    assert D(rebate["cent_rounded_total"]) == D("0")


def test_fresh_pair_model_poison_edge_when_threshold_fades():
    readiness_plan = plan_threshold_quotes(D("0.60"), D("0.65"))
    model = sup._patched_pair_model(readiness_plan, D("0.01"))
    # Fresh book is now balanced; no live order should survive the child's 1c gate.
    faded = model(D("0.48"), D("0.52"))
    assert faded["modeled_edge"] == D("-1")


def test_fresh_pair_model_keeps_edge_when_threshold_still_actionable():
    readiness_plan = plan_threshold_quotes(D("0.60"), D("0.65"))
    model = sup._patched_pair_model(readiness_plan, D("0.01"))
    fresh = model(D("0.61"), D("0.66"))
    assert fresh["modeled_edge"] >= D("0.01")


def test_child_ids_are_bounded_and_horizon_specific():
    assert sup.child_id("example-session", "5m", 1).startswith("l5v1-")
    assert "-5m-001" in sup.child_id("example-session", "5m", 1)
    assert "-15m-001" in sup.child_id("example-session", "15m", 1)
