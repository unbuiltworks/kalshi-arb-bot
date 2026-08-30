from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src.gemini_prediction_book_cache import BookSnapshot
from src import gemini_q10_production_executor_v5_2 as mod
from src import gemini_q10_v5_2_recovery as recovery52
from src import gemini_q10_v5_2_deadman as deadman52


@pytest.mark.asyncio
async def test_reliable_fresh_top_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def flaky(symbol: str, timeout: float):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return (D("0.40"), D("20"), D("0.60"), D("20"))

    async def no_sleep(_seconds: float):
        return None

    monkeypatch.setattr(mod, "_ORIGINAL_FRESH_TOP", flaky)
    monkeypatch.setattr(mod.depth_retry.asyncio, "sleep", no_sleep)
    got = await mod.reliable_fresh_top("GEMI-BTC05M-TEST-UP")
    assert got[0] == D("0.40")
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_qualifier_retry_logs_message(monkeypatch):
    calls = {"n": 0}
    emitted = []
    expected = (object(), {"event": {}}, {"locked_edge": D("1")})

    async def candidate():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("no fresh Gemini BTC5m depth after 3 independent attempts")
        return expected

    async def no_sleep(_seconds: float):
        return None

    monkeypatch.setattr(mod.v5.v1.probe, "_candidate", candidate)
    monkeypatch.setattr(mod.v5.v1.probe, "emit", lambda stage, **kw: emitted.append((stage, kw)))
    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    got = await mod.wait_candidate_v52()
    assert got is expected
    assert emitted[0][0] == "QUALIFIER_RETRY"
    assert "3 independent attempts" in emitted[0][1]["error_message"]
    assert emitted[0][1]["order_entry"] is False


def test_external_depth_subtracts_our_resting_q10():
    snap = BookSnapshot(
        bid=D("0.40"), bid_size=D("10"), ask=D("0.60"), ask_size=D("10"),
        age_ms=1.0, received_ns=1, sequence=1,
    )
    model = {"yes_quote": D("0.40"), "no_quote": D("0.40")}
    bid_size, ask_size = mod._external_depth_snapshot(
        model, snap, yes_resting=True, no_resting=True,
    )
    assert bid_size == D("0")
    assert ask_size == D("0")


@pytest.mark.asyncio
async def test_order_history_window_covers_600_seconds(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        def json(self):
            return {"orders": []}

    async def fake_request(method, path, *, params, timeout):
        calls.append((path, dict(params)))
        return Response()

    monkeypatch.setattr(mod.xrp_maker, "private_rest_request", fake_request)
    monkeypatch.setattr(mod.time, "time", lambda: 1_000_000.0)
    assert await mod.order_by_client_v52("cid", "SYM") is None
    history = [params for path, params in calls if path.endswith("/history")]
    assert len(history) == 2
    now_ms = 1_000_000_000
    assert all(now_ms - params["from"] == 600_000 for params in history)


@dataclass
class FakeRoute:
    safe: bool = True
    side: str = "BUY"
    limit_price: D = D("0.50")
    outcome: str = "NO"
    action: str = "COMPLETE"

    def as_dict(self):
        return {
            "safe": self.safe,
            "side": self.side,
            "limit_price": str(self.limit_price),
            "outcome": self.outcome,
            "action": self.action,
        }


@pytest.mark.asyncio
async def test_acked_unfindable_fok_hard_halts_without_second_attempt(monkeypatch):
    snap = BookSnapshot(
        bid=D("0.40"), bid_size=D("100"), ask=D("0.60"), ask_size=D("100"),
        age_ms=1.0, received_ns=1, sequence=1,
    )

    class Cache:
        async def wait_fresh(self, **_kwargs):
            return snap

    class Maker:
        def __init__(self):
            self.requests = 0
        async def request(self, method, params, timeout):
            self.requests += 1
            return {"status": 200}

    maker = Maker()
    heartbeats = []
    monkeypatch.setattr(mod.rescue, "best_route_after_fill", lambda **_kw: FakeRoute())
    monkeypatch.setattr(mod.v5.v1, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(mod.v5, "_heartbeat", lambda state, **kw: heartbeats.append(kw))

    async def no_row(_client_id, _symbol):
        return None

    monkeypatch.setattr(mod.v5.v1.base, "_find_row", no_row)
    result = await mod.bounded_fok_v52(
        maker, cache=Cache(), symbol="SYM", filled_outcome="YES",
        filled_price=D("0.40"), quantity=D("10"), increment=D("0.01"),
        trade_id="trade", state={},
    )
    assert result["status"] == "RESCUE_FOK_STATUS_UNKNOWN"
    assert result["hard_halt"] is True
    assert result["flattened"] is False
    assert maker.requests == 1
    assert any(h.get("stage") == "RESCUE_FOK_STATUS_UNKNOWN" for h in heartbeats)


@pytest.mark.asyncio
async def test_pending_rescue_makes_recovery_residual_ambiguous(monkeypatch):
    report = recovery52.recovery.RecoveryAudit(
        status="OPEN_STATE_EXACT_RESIDUAL", open_state=True, symbol="SYM",
        yes_fill=D("10"), no_fill=D("0"), residual_outcome="YES",
        residual_quantity=D("10"), exact_residual=True, active_known_orders=0,
        account_positions=1, account_orders=0,
    )
    state = {"open": True, "pending_rescue": {"client_order_id": "fok-1"}}

    async def original_audit():
        return report, state, {}, {}

    monkeypatch.setattr(recovery52, "_ORIGINAL_AUDIT", original_audit)
    monkeypatch.setattr(recovery52.v52, "install", lambda: None)
    got, _, _, _ = await recovery52.audit()
    assert got.exact_residual is False
    assert got.status == "OPEN_STATE_PENDING_RESCUE_AMBIGUOUS"


@pytest.mark.asyncio
async def test_deadman_does_not_terminal_exit_when_account_not_flat(monkeypatch):
    report = recovery52.recovery.RecoveryAudit(
        status="OPEN_STATE_FLAT_FILLS", open_state=True, symbol="SYM",
        yes_fill=D("10"), no_fill=D("10"), residual_outcome=None,
        residual_quantity=D("0"), exact_residual=True, active_known_orders=0,
        account_positions=1, account_orders=0,
    )

    monkeypatch.setattr(deadman52.v52, "install", lambda: None)
    monkeypatch.setattr(deadman52.recovery52, "install", lambda: None)
    monkeypatch.setattr(deadman52.v52.v5.v1, "_read_state", lambda: {"open": True})
    monkeypatch.setattr(deadman52.recovery52.recovery, "_is_open_state", lambda _s: True)
    monkeypatch.setattr(deadman52, "heartbeat_age_seconds", lambda _s: 999.0)
    monkeypatch.setattr(deadman52, "watchdog_armed", lambda: True)
    monkeypatch.setattr(deadman52.recovery52.recovery, "recovery_armed", lambda: True)

    async def audit():
        return report, {}, {}, {}

    async def recover():
        return {"status": "RECOVERY_CALLED"}

    monkeypatch.setattr(deadman52.recovery52, "audit", audit)
    monkeypatch.setattr(deadman52.recovery52, "recover", recover)
    result = await deadman52.check_once()
    assert result["status"] == "DEADMAN_INTERVENTION"
    assert result["intervened"] is True


def test_install_wires_v52_hot_path(monkeypatch):
    monkeypatch.setattr(mod.v51, "install", lambda: None)
    mod.install()
    assert mod.v5.v1.base.fresh_top is mod.reliable_fresh_top
    assert mod.v5.v1.probe._wait_candidate is mod.wait_candidate_v52
    assert mod.v5.v1.base._order_by_client is mod.order_by_client_v52
    assert mod.v5._bounded_fok is mod.bounded_fok_v52
    assert mod.v5._run_campaign is mod.run_campaign_v52
