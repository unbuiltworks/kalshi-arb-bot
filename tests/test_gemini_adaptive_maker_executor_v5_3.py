from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import gemini_adaptive_maker_executor_v5_3 as mod
from src import gemini_adaptive_maker_v5_3_recovery as recovery53
from src import gemini_adaptive_maker_v5_3_deadman as deadman53
from src.gemini_adaptive_maker_policy_v5_3 import AdaptiveMakerPlan
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot


@dataclass
class Event:
    ticker: str = "BTC05MTEST"
    symbol: str = "GEMI-BTC05MTEST-UP"
    quantity_minimum: D = D("1")
    quantity_increment: D = D("1")
    price_increment: D = D("0.01")
    seconds_to_expiry: float = 275.0


@dataclass
class FakeRoute:
    safe: bool = True
    side: str = "SELL"
    outcome: str = "YES"
    action: str = "UNWIND"
    limit_price: D = D("0.13")
    current_execution_price: D = D("0.13")
    available_top_quantity: D = D("100")
    pnl_at_current: D = D("-0.20")
    pnl_at_limit: D = D("-0.20")
    loss_at_current: D = D("0.20")
    loss_at_limit: D = D("0.20")
    depth_ok: bool = True

    def as_dict(self):
        return {k: str(v) if isinstance(v, D) else v for k, v in self.__dict__.items()}


def plan(q: str = "3") -> AdaptiveMakerPlan:
    route = FakeRoute()
    return AdaptiveMakerPlan(
        quantity=D(q),
        yes_quote=D("0.14"),
        no_quote=D("0.54"),
        pair_profit=D("0.90"),
        edge_floor=D("0.15"),
        worst_rescue_loss=D("0.20"),
        yes_fill_route=route,  # type: ignore[arg-type]
        no_fill_route=route,  # type: ignore[arg-type]
        yes_rebate_eligible=False,
        no_rebate_eligible=True,
        yes_improvement=D("0.01"),
        no_improvement=D("0"),
    )


def snap(sequence: int = 1) -> DepthSnapshot:
    return DepthSnapshot(
        bids=((D("0.13"), D("100")),),
        asks=((D("0.46"), D("100")),),
        age_ms=1.0,
        received_ns=1,
        sequence=sequence,
    )


class Cache:
    def __init__(self):
        self.s = snap(1)
        self.last_error = None

    async def wait_fresh(self, **_kwargs):
        return self.s

    async def wait_newer(self, after_sequence, **_kwargs):
        self.s = snap(max(int(after_sequence) + 1, self.s.sequence + 1))
        return self.s

    def snapshot(self):
        return self.s


class Maker:
    def __init__(self):
        self.ws = None
        self.reader = SimpleNamespace(done=lambda: False)
        self.events = asyncio.Queue()

    async def connect(self):
        return None

    async def close(self):
        return None


class Guard:
    def decision(self):
        return SimpleNamespace(ready=True, dangerous=False)


def heartbeat(state, *, stage=None, **fields):
    if stage is not None:
        state["status"] = stage
    state.update(fields)


@pytest.mark.asyncio
async def test_candidate_path_never_calls_reward_endpoint(monkeypatch):
    event = Event()
    cache = Cache()

    async def discover():
        return event

    class CacheFactory:
        def __init__(self, *_args, **_kwargs):
            self.last_error = None
        async def run(self, stop):
            await stop.wait()
        async def wait_fresh(self, **_kwargs):
            return snap()

    async def reward_should_never_run(*_args, **_kwargs):
        raise AssertionError("reward endpoint must not be part of V5.3 admission")

    monkeypatch.setattr(mod.v5.v1.base, "discover_btc5m", discover)
    monkeypatch.setattr(mod, "GeminiDepth5Cache", CacheFactory)
    monkeypatch.setattr(mod.policy, "select_plan", lambda **_kw: plan("3"))
    monkeypatch.setattr(mod.v5.v1.probe, "_reward_snapshot", reward_should_never_run)
    monkeypatch.setattr(mod.v5.v1, "emit", lambda *_a, **_kw: None)

    found = await mod.wait_adaptive_candidate()
    assert found is not None
    got_event, _, stop, task, got_plan = found
    assert got_event.symbol == event.symbol
    assert got_plan.quantity == D("3")
    stop.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _base_campaign_patches(monkeypatch, selected_plan: AdaptiveMakerPlan):
    cache = Cache()
    event = Event()

    async def candidate():
        return event, cache, asyncio.Event(), None, selected_plan

    async def preflight():
        return D("100"), {"orders": 0, "positions": 0, "order_symbols": [], "position_symbols": []}

    monkeypatch.setattr(mod, "wait_adaptive_candidate", candidate)
    monkeypatch.setattr(mod.v5.v1, "_preflight_flat", preflight)
    monkeypatch.setattr(mod.policy, "select_plan", lambda **_kw: selected_plan)
    monkeypatch.setattr(mod.v5.v1.base, "GeminiMakerSession", Maker)
    monkeypatch.setattr(mod.v5, "_heartbeat", heartbeat)
    monkeypatch.setattr(mod.v5.v1, "emit", lambda *_a, **_kw: None)
    monkeypatch.setattr(mod, "asdict", lambda _x: {})
    monkeypatch.setenv(mod.v5.v1.ARM_ID_ENV, "test")
    return event, cache


@pytest.mark.asyncio
async def test_selected_quantity_is_used_in_first_order_and_unresolved_yes_stops(monkeypatch):
    selected = plan("3")
    await _base_campaign_patches(monkeypatch, selected)
    posts = []

    async def post(_maker, **kwargs):
        posts.append(kwargs)
        return {"status": 200}, None, 1.0

    monkeypatch.setattr(mod.v5.v1, "_post", post)
    result = await mod.run_campaign_v53(guard=Guard(), campaign_no=1)
    assert result["status"] == "YES_ACK_ROW_UNRESOLVED"
    assert result["hard_halt"] is True
    assert len(posts) == 1
    assert posts[0]["quantity"] == D("3")


@pytest.mark.asyncio
async def test_unresolved_no_ack_hard_halts_after_exact_selected_yes(monkeypatch):
    selected = plan("3")
    await _base_campaign_patches(monkeypatch, selected)
    posts = []

    async def post(_maker, **kwargs):
        posts.append(kwargs)
        if kwargs["outcome"] == "YES":
            return {"status": 200}, {"clientOrderId": kwargs["client_id"], "filledQuantity": "0"}, 1.0
        return {"status": 200}, None, 1.0

    monkeypatch.setattr(mod.v5.v1, "_post", post)
    monkeypatch.setattr(mod, "_route_after_fill_external", lambda **_kw: FakeRoute(safe=True))
    result = await mod.run_campaign_v53(guard=Guard(), campaign_no=1)
    assert result["status"] == "NO_ACK_ROW_UNRESOLVED"
    assert result["hard_halt"] is True
    assert [p["quantity"] for p in posts] == [D("3"), D("3")]


@pytest.mark.asyncio
async def test_fill_above_selected_quantity_hard_halts(monkeypatch):
    selected = plan("3")
    await _base_campaign_patches(monkeypatch, selected)
    yes_id = "gadv53-test-1-yes"

    class FillMaker(Maker):
        def __init__(self):
            super().__init__()
            self.events.put_nowait({"c": yes_id, "Z": "1", "X": "PARTIALLY_FILLED"})

    monkeypatch.setattr(mod.v5.v1.base, "GeminiMakerSession", FillMaker)

    async def post(_maker, **kwargs):
        return {"status": 200}, {"clientOrderId": kwargs["client_id"], "filledQuantity": "0"}, 1.0

    async def cancel_pair(*_args, **_kwargs):
        return D("4"), D("0"), {"filledQuantity": "4"}, {"filledQuantity": "0"}

    monkeypatch.setattr(mod.v5.v1, "_post", post)
    monkeypatch.setattr(mod, "_route_after_fill_external", lambda **_kw: FakeRoute(safe=True))
    monkeypatch.setattr(mod.policy, "standing_rescue_for_plan", lambda **_kw: SimpleNamespace(safe=True))
    monkeypatch.setattr(mod.v5, "_cancel_original_pair", cancel_pair)
    result = await mod.run_campaign_v53(guard=Guard(), campaign_no=1)
    assert result["status"] == "SELECTED_QUANTITY_BOUND_VIOLATION"
    assert result["hard_halt"] is True


@pytest.mark.asyncio
async def test_grace_ack_200_with_unresolved_row_halts_without_inferred_zero(monkeypatch):
    cache = Cache()
    maker = Maker()
    state = {}

    monkeypatch.setattr(mod.rescue, "best_route_after_fill", lambda **_kw: FakeRoute(safe=True))
    monkeypatch.setattr(mod.rescue, "maker_grace_seconds", lambda **_kw: 1.0)
    monkeypatch.setattr(mod.v5, "_heartbeat", heartbeat)
    monkeypatch.setattr(mod.v5.v1, "emit", lambda *_a, **_kw: None)

    async def post(_maker, **_kwargs):
        return {"status": 200}, None, 1.0

    monkeypatch.setattr(mod.v5.v1, "_post", post)
    result = await mod.calm_maker_grace_v53(
        maker,
        cache=cache,
        guard=Guard(),
        symbol="SYM",
        filled_outcome="YES",
        filled_price=D("0.14"),
        quantity=D("3"),
        increment=D("0.01"),
        trade_id="trade",
        state=state,
    )
    assert result["status"] == "GRACE_STATUS_UNKNOWN"
    assert result["hard_halt"] is True
    assert result["maker_filled"] is None
    assert state.get("pending_grace") is not None


@pytest.mark.asyncio
async def test_recovery_blocks_pending_grace(monkeypatch):
    report = recovery53.recovery52.recovery.RecoveryAudit(
        status="OPEN_STATE_EXACT_RESIDUAL",
        open_state=True,
        symbol="SYM",
        yes_fill=D("3"),
        no_fill=D("0"),
        residual_outcome="YES",
        residual_quantity=D("3"),
        exact_residual=True,
        active_known_orders=0,
        account_positions=1,
        account_orders=0,
    )
    state = {
        "open": True,
        "classification": recovery53.V53_CLASSIFICATION,
        "quantity": "3",
        "pending_grace": {"client_order_id": "grace-1"},
    }

    async def original_audit():
        return report, state, {}, {}

    monkeypatch.setattr(recovery53, "_ORIGINAL_AUDIT", original_audit)
    monkeypatch.setattr(recovery53.v53, "install", lambda: None)
    got, _, _, _ = await recovery53.audit()
    assert got.status == "OPEN_STATE_PENDING_GRACE_AMBIGUOUS"
    assert got.exact_residual is False


@pytest.mark.asyncio
async def test_recovery_blocks_residual_larger_than_selected_quantity(monkeypatch):
    report = recovery53.recovery52.recovery.RecoveryAudit(
        status="OPEN_STATE_EXACT_RESIDUAL",
        open_state=True,
        symbol="SYM",
        yes_fill=D("4"),
        no_fill=D("0"),
        residual_outcome="YES",
        residual_quantity=D("4"),
        exact_residual=True,
        active_known_orders=0,
        account_positions=1,
        account_orders=0,
    )
    state = {
        "open": True,
        "classification": recovery53.V53_CLASSIFICATION,
        "quantity": "3",
    }

    async def original_audit():
        return report, state, {}, {}

    monkeypatch.setattr(recovery53, "_ORIGINAL_AUDIT", original_audit)
    monkeypatch.setattr(recovery53.v53, "install", lambda: None)
    got, _, _, _ = await recovery53.audit()
    assert got.status == "OPEN_STATE_RESIDUAL_EXCEEDS_SELECTED_QUANTITY"
    assert got.exact_residual is False


@pytest.mark.asyncio
async def test_deadman_requires_account_flat_before_no_residual_terminal(monkeypatch):
    report = recovery53.recovery52.recovery.RecoveryAudit(
        status="OPEN_STATE_FLAT_FILLS",
        open_state=True,
        symbol="SYM",
        yes_fill=D("3"),
        no_fill=D("3"),
        residual_outcome=None,
        residual_quantity=D("0"),
        exact_residual=True,
        active_known_orders=0,
        account_positions=1,
        account_orders=0,
    )
    monkeypatch.setattr(deadman53.v53, "install", lambda: None)
    monkeypatch.setattr(deadman53.recovery53, "install", lambda: None)
    monkeypatch.setattr(deadman53.v53.v5.v1, "_read_state", lambda: {"open": True})
    monkeypatch.setattr(deadman53.recovery53.recovery52.recovery, "_is_open_state", lambda _s: True)
    monkeypatch.setattr(deadman53, "heartbeat_age_seconds", lambda _s: 999.0)
    monkeypatch.setattr(deadman53, "watchdog_armed", lambda: True)
    monkeypatch.setattr(deadman53.recovery53.recovery52.recovery, "recovery_armed", lambda: True)

    async def audit():
        return report, {}, {}, {}

    async def recover():
        return {"status": "RECOVERY_CALLED"}

    monkeypatch.setattr(deadman53.recovery53, "audit", audit)
    monkeypatch.setattr(deadman53.recovery53, "recover", recover)
    result = await deadman53.check_once()
    assert result["status"] == "DEADMAN_INTERVENTION"
    assert result["intervened"] is True


def test_install_keeps_v52_safety_and_replaces_campaign(monkeypatch):
    called = {"v52": 0}
    monkeypatch.setattr(mod.v52, "install", lambda: called.__setitem__("v52", called["v52"] + 1))
    mod.install()
    assert called["v52"] == 1
    assert mod.v5._run_campaign is mod.run_campaign_v53
