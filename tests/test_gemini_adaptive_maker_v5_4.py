import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_daily_risk_v5_4 as daily
from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_4 as v54
from src import gemini_adaptive_maker_v5_3_runtime as runtime
from src import gemini_q10_production_executor_v5_2 as v52
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot


def snap(*, bids, asks):
    return DepthSnapshot(
        bids=tuple((D(str(p)), D(str(q))) for p, q in bids),
        asks=tuple((D(str(p)), D(str(q))) for p, q in asks),
        age_ms=10.0,
        received_ns=1,
        sequence=1,
    )


def event(*, q_min="0.01", q_inc="0.01", p_inc="0.01"):
    return SimpleNamespace(
        quantity_minimum=D(q_min),
        quantity_increment=D(q_inc),
        price_increment=D(p_inc),
    )


def test_incident_book_downsizes_to_hard_campaign_loss_bound(monkeypatch):
    monkeypatch.setenv("GEMINI_Q10_V5_MAX_CAMPAIGN_LOSS_USD", "1.00")
    e = event()
    assert grid.event_contract_ok(e)
    plan = grid.select_plan_grid_aware(
        snap=snap(bids=[("0.41", "100")], asks=[("0.61", "100")]),
        increment=D("0.01"),
    )
    assert plan is not None
    assert plan.quantity == D("2")
    assert grid.catastrophic_loss_for_plan(plan) <= D("1.00")
    assert grid.single_leg_settlement_loss(price=plan.yes_quote, quantity=D("3")) > D("1.00")


def test_campaign_bound_is_independent_of_normal_rescue_bound(monkeypatch):
    monkeypatch.setenv("GEMINI_Q10_V5_MAX_RESCUE_LOSS_USD", "0.30")
    monkeypatch.setenv("GEMINI_Q10_V5_MAX_CAMPAIGN_LOSS_USD", "1.00")
    e = event()
    assert grid.event_contract_ok(e)
    plan = grid.select_plan_grid_aware(
        snap=snap(bids=[("0.41", "100")], asks=[("0.61", "100")]),
        increment=D("0.01"),
    )
    assert plan is not None
    assert plan.worst_rescue_loss <= D("0.30")
    assert grid.catastrophic_loss_for_plan(plan) <= D("1.00")


def test_grid_context_fails_closed_until_live_event_metadata_is_seen(monkeypatch):
    monkeypatch.setattr(grid, "_current_valid_quantities", None)
    assert grid.select_plan_grid_aware(
        snap=snap(bids=[("0.41", "100")], asks=[("0.61", "100")]),
        increment=D("0.01"),
    ) is None


def test_runtime_reasserts_v54_hook_after_legacy_v52_install():
    runtime.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v54.run_campaign_v54
    assert v53._event_contract_ok is grid.event_contract_ok
    assert v53.policy.select_plan is grid.select_plan_grid_aware

    v52.install()
    assert v53.v5._run_campaign is not v54.run_campaign_v54

    runtime.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v54.run_campaign_v54
    assert v53._event_contract_ok is grid.event_contract_ok
    assert v53.policy.select_plan is grid.select_plan_grid_aware


def test_runtime_reasserts_after_audit_before_run(monkeypatch):
    async def fake_audit():
        v52.install()
        return SimpleNamespace(open_state=False), None, None, None

    async def fake_run():
        assert v53.v5._run_campaign is v54.run_campaign_v54
        assert v53._event_contract_ok is grid.event_contract_ok
        assert v53.policy.select_plan is grid.select_plan_grid_aware

    monkeypatch.setattr(runtime.recovery53, "audit", fake_audit)
    monkeypatch.setattr(v53.v5, "run", fake_run)
    asyncio.run(runtime.run())


def test_missing_global_daily_ledger_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(daily.RISK_DIR_ENV, str(tmp_path))
    decision = daily.check(
        current_available_usd=D("48.0255"),
        loss_limit_usd=D("3.00"),
        account_flat=True,
        allow_rollover=True,
        now=datetime(2026, 8, 30, 21, tzinfo=timezone.utc),
        persist_observation=False,
    )
    assert decision.blocked
    assert decision.status == "RISK_LEDGER_MISSING"


def test_verified_canary_loss_blocks_same_day_across_processes(tmp_path, monkeypatch):
    monkeypatch.setenv(daily.RISK_DIR_ENV, str(tmp_path))
    daily.seed(
        date_utc="2026-08-30",
        baseline_available_usd=D("52.3155"),
        current_available_usd=D("48.0255"),
        source="V53_CANARY1_VERIFIED_ACCOUNT_BASELINE",
    )
    for _ in range(2):
        decision = daily.check(
            current_available_usd=D("48.0255"),
            loss_limit_usd=D("3.00"),
            account_flat=True,
            allow_rollover=True,
            now=datetime(2026, 8, 30, 22, tzinfo=timezone.utc),
        )
        assert decision.blocked
        assert decision.status == "DAILY_LOSS_STOP_GLOBAL"
        assert decision.baseline_available_usd == D("52.3155")
        assert decision.drawdown_usd == D("4.2900")


def test_global_daily_ledger_rolls_only_on_new_utc_day_flat_account(tmp_path, monkeypatch):
    monkeypatch.setenv(daily.RISK_DIR_ENV, str(tmp_path))
    daily.seed(
        date_utc="2026-08-30",
        baseline_available_usd=D("52.3155"),
        current_available_usd=D("48.0255"),
        source="TEST",
    )
    decision = daily.check(
        current_available_usd=D("48.0255"),
        loss_limit_usd=D("3.00"),
        account_flat=True,
        allow_rollover=True,
        now=datetime(2026, 8, 31, 0, 1, tzinfo=timezone.utc),
    )
    assert not decision.blocked
    assert decision.rolled_over
    assert decision.baseline_available_usd == D("48.0255")
    assert decision.drawdown_usd == D("0")


def test_v54_campaign_wrapper_blocks_before_candidate_when_daily_stop_hit(tmp_path, monkeypatch):
    monkeypatch.setenv(daily.RISK_DIR_ENV, str(tmp_path))
    monkeypatch.setenv("GEMINI_Q10_V5_MAX_DAILY_LOSS_USD", "3.00")
    daily.seed(
        date_utc=daily._today_utc(),
        baseline_available_usd=D("52.3155"),
        current_available_usd=D("48.0255"),
        source="V53_CANARY1_VERIFIED_ACCOUNT_BASELINE",
    )

    async def fake_preflight():
        return D("48.0255"), {"orders": 0, "positions": 0, "order_symbols": [], "position_symbols": []}

    async def forbidden_campaign(**kwargs):
        raise AssertionError("underlying campaign must not run after global daily stop")

    monkeypatch.setattr(v53.v5.v1, "_preflight_flat", fake_preflight)
    monkeypatch.setattr(v53, "run_campaign_v53", forbidden_campaign)
    result = asyncio.run(v54.run_campaign_v54(guard=object(), campaign_no=1))
    assert result["status"] == "DAILY_LOSS_STOP_GLOBAL"
    assert result["hard_halt"] is True
    assert result["order_entry"] is False
