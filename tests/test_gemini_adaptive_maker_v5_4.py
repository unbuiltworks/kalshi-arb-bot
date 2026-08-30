import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_executor_v5_3 as v53
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
    # Live-public V5.3 qualification shape. The failed canary later filled near this
    # price region. q10 had a >$4 worst settlement loss, so it must not be admitted
    # under the configured $1 campaign ceiling even though instantaneous rescue is safe.
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


def test_runtime_reasserts_adaptive_hook_after_legacy_v52_install():
    runtime.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v53.run_campaign_v53
    assert v53._event_contract_ok is grid.event_contract_ok
    assert v53.policy.select_plan is grid.select_plan_grid_aware

    # This is the exact class of clobber that caused the Aug 30 incident.
    v52.install()
    assert v53.v5._run_campaign is not v53.run_campaign_v53

    runtime.install_new_campaign_runtime()
    assert v53.v5._run_campaign is v53.run_campaign_v53
    assert v53._event_contract_ok is grid.event_contract_ok
    assert v53.policy.select_plan is grid.select_plan_grid_aware


def test_runtime_reasserts_after_audit_before_run(monkeypatch):
    async def fake_audit():
        # Model the legacy recovery audit side effect that caused the incident.
        v52.install()
        return SimpleNamespace(open_state=False), None, None, None

    async def fake_run():
        assert v53.v5._run_campaign is v53.run_campaign_v53
        assert v53._event_contract_ok is grid.event_contract_ok
        assert v53.policy.select_plan is grid.select_plan_grid_aware

    monkeypatch.setattr(runtime.recovery53, "audit", fake_audit)
    monkeypatch.setattr(v53.v5, "run", fake_run)
    asyncio.run(runtime.run())
