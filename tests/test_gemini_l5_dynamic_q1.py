import asyncio
import json
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import gemini_l5_dynamic_q1 as m


class Response:
    status_code = 200
    def __init__(self, body):
        self._body = body
    def json(self):
        return self._body


def candidate(asset="ETH", edge="0.02"):
    return m.selector.PublicCandidate(
        asset=asset,
        event=SimpleNamespace(ticker=f"{asset}05M", symbol=f"{asset}-UP", expiry_ms=9999999999999),
        bid=D("0.48"),
        bid_size=D("100"),
        ask=D("0.52"),
        ask_size=D("100"),
        model={
            "yes": D("0.47"), "no": D("0.49"), "edge": D(edge),
            "yes_backoff": D("0.01"), "no_backoff": D("0.01"),
            "total_backoff": D("0.02"), "preferred_backoff_side": "YES",
            "preferred_backoff": D("0.01"), "completion_reserve_ticks": 1,
        },
    )


def test_disarmed_makes_no_venue_call(monkeypatch):
    monkeypatch.setattr(m, "armed", lambda: False)
    async def boom(_allowed):
        raise AssertionError("venue call while disarmed")
    monkeypatch.setattr(m, "account_guard", boom)
    asyncio.run(m.run())


def test_account_guard_requires_zero_active_orders(monkeypatch):
    async def fake(_method, path, *, params, timeout):
        if path.endswith("/active"):
            return Response({"orders": [{"orderId": "1"}]})
        return Response({"positions": []})
    monkeypatch.setattr(m, "private_rest_request", fake)
    with pytest.raises(RuntimeError, match="unexpected active prediction orders=1"):
        asyncio.run(m.account_guard(set()))


def test_account_guard_startup_requires_flat_account(monkeypatch):
    async def fake(_method, path, *, params, timeout):
        if path.endswith("/active"):
            return Response({"orders": []})
        return Response({"positions": [{"symbol": "ETH-UP", "eventOutcome": "yes", "totalQuantity": "1"}]})
    monkeypatch.setattr(m, "private_rest_request", fake)
    with pytest.raises(RuntimeError, match="unexpected prediction position"):
        asyncio.run(m.account_guard(set()))


def test_account_guard_allows_only_balanced_session_positions(monkeypatch):
    async def fake(_method, path, *, params, timeout):
        if path.endswith("/active"):
            return Response({"orders": []})
        return Response({"positions": [
            {"symbol": "ETH-UP", "eventOutcome": "yes", "totalQuantity": "3"},
            {"symbol": "ETH-UP", "eventOutcome": "no", "totalQuantity": "3"},
        ]})
    monkeypatch.setattr(m, "private_rest_request", fake)
    out = asyncio.run(m.account_guard({"ETH-UP"}))
    assert out["active_orders"] == 0
    assert len(out["positions"]) == 2


def test_account_guard_rejects_unbalanced_session_positions(monkeypatch):
    async def fake(_method, path, *, params, timeout):
        if path.endswith("/active"):
            return Response({"orders": []})
        return Response({"positions": [
            {"symbol": "ETH-UP", "eventOutcome": "yes", "totalQuantity": "2"},
            {"symbol": "ETH-UP", "eventOutcome": "no", "totalQuantity": "1"},
        ]})
    monkeypatch.setattr(m, "private_rest_request", fake)
    with pytest.raises(RuntimeError, match="unbalanced prediction position"):
        asyncio.run(m.account_guard({"ETH-UP"}))


def test_run_cycle_uses_unique_inner_session_and_selected_event(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "MIN_TOP_PAIR_EDGE", D("0.01"))
    selected = candidate("SOL")
    original_discover = m.pair.discover_eth5m
    seen = {}

    async def fake_run(*, min_edge, first_wait, complement_wait, min_tte):
        seen["sid"] = m.os.environ[m.pair.SID_ENV]
        seen["min_edge"] = min_edge
        seen["first_wait"] = first_wait
        event = await m.pair.discover_eth5m()
        seen["event"] = event.ticker
        path = m.Path(m.os.environ[m.pair.STATE_ENV])
        path.write_text(json.dumps({
            "terminal": True,
            "status": "MATCHED",
            "symbol": "SOL-UP",
            "event": "SOL05M",
            "entry_edge": "0.02",
            "yes_qty": "1",
            "no_qty": "1",
            "maker_turnover": "0.96",
            "maker_fees": "0.02",
        }))
    monkeypatch.setattr(m.pair, "run", fake_run)
    out = asyncio.run(m._run_cycle("outer", 7, tmp_path, selected=selected))
    assert seen["sid"] == "outer-c007"
    assert seen["min_edge"] == D("0.01")
    assert seen["first_wait"] == m.FIRST_WAIT_SECONDS
    assert seen["event"] == "SOL05M"
    assert out["status"] == "MATCHED"
    assert out["selected_asset"] == "SOL"
    assert m.pair.discover_eth5m is original_discover


def test_public_idle_scan_does_not_add_private_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "public-idle-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 1.0)
    monkeypatch.setattr(m, "IDLE_POLL_SECONDS", 0.0)
    clock = [0.0]
    guards = []
    scans = []

    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])

    async def flat(allowed):
        guards.append(set(allowed))
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def no_candidate(**_kwargs):
        scans.append(True)
        clock[0] = 2.0
        return None

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m.selector, "select_best_candidate", no_candidate)
    monkeypatch.setattr(m.asyncio, "sleep", no_sleep)
    asyncio.run(m.run())

    assert len(scans) == 1
    # Startup + final guard only. There is no per-idle-scan private guard.
    assert len(guards) == 2


def test_outer_accepts_dynamic_completion_match_and_records_rescue(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "dynamic-rescue-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 1)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("12"))
    monkeypatch.setattr(m, "CYCLE_PAUSE_SECONDS", 0.0)
    chosen = candidate("ETH")
    seen_allowed = []

    async def choose(**_kwargs):
        return chosen

    async def flat(allowed):
        seen_allowed.append(set(allowed))
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def matched(_sid, _number, _state_dir, *, selected=None):
        assert selected is chosen
        return {
            "terminal": True,
            "status": "MATCHED_DYNAMIC_COMPLETION",
            "rescue_status": "MATCHED",
            "completion_reserve_ticks": 1,
            "completion_reprices": 1,
            "selected_asset": "ETH",
            "symbol": "ETH-UP",
            "event": "ETH05M",
            "yes_backoff": "0.01",
            "no_backoff": "0.01",
            "total_backoff": "0.02",
            "preferred_backoff_side": "NO",
            "entry_edge": "0.02",
            "yes_qty": "1",
            "no_qty": "1",
            "maker_turnover": "0.97",
            "maker_fees": "0.02",
        }

    monkeypatch.setattr(m.selector, "select_best_candidate", choose)
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", matched)
    asyncio.run(m.run())

    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["campaigns"][0]["asset"] == "ETH"
    assert state["campaigns"][0]["status"] == "MATCHED_DYNAMIC_COMPLETION"
    assert state["campaigns"][0]["rescue_status"] == "MATCHED"
    assert state["campaigns"][0]["completion_reprices"] == 1
    assert state["campaigns"][0]["realized"] is False
    assert {"ETH-UP"} in seen_allowed


def test_dynamic_halts_immediately_on_q1_residual(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "dynamic-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 3)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("12"))
    chosen = candidate("BTC")

    async def choose(**_kwargs):
        return chosen

    async def flat(_allowed):
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def residual(_sid, _number, _state_dir, *, selected=None):
        assert selected is chosen
        return {
            "terminal": True,
            "status": "ONE_SIDED_RESIDUAL",
            "selected_asset": "BTC",
            "symbol": "BTC-UP",
            "event": "BTC05M",
            "entry_edge": "0.02",
            "yes_qty": "1",
            "no_qty": "0",
            "maker_turnover": "0.47",
            "maker_fees": "0.01",
        }

    monkeypatch.setattr(m.selector, "select_best_candidate", choose)
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", residual)

    with pytest.raises(RuntimeError, match="halted on residual"):
        asyncio.run(m.run())
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["terminal_reason"] == "RESIDUAL_DETECTED"
    assert state["campaigns"][0]["asset"] == "BTC"
    assert state["campaigns"][0]["residual_qty"] == "1"


def test_outer_accepts_v1_completion_match_end_to_end(monkeypatch, tmp_path):
    """Simulated full owner session: a MATCHED_V1_COMPLETION cycle must
    terminate the session cleanly (matched-cap reached), never as
    UNSAFE_CYCLE_STATUS_*."""
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "v1-match-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 1)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("1"))
    monkeypatch.setattr(m, "CYCLE_PAUSE_SECONDS", 0.0)
    chosen = candidate("BTC")

    async def choose(**_kwargs):
        return chosen

    async def flat(_allowed):
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def v1_matched(_sid, _number, _state_dir, *, selected=None):
        return {
            "terminal": True,
            "status": "MATCHED_V1_COMPLETION",
            "driver": "V1_POLICY",
            "initializer": "TRANSFER_INITIALIZER",
            "selected_asset": "BTC",
            "symbol": "BTC-UP",
            "event": "BTC05M",
            "entry_edge": "0.01",
            "yes_qty": "1",
            "no_qty": "1",
            "maker_turnover": "0.97",
            "maker_fees": "0.02",
            "first_fill_exec_price": "0.48",
        }

    monkeypatch.setattr(m.selector, "select_best_candidate", choose)
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", v1_matched)
    asyncio.run(m.run())    # must not raise

    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["terminal_reason"] == "MATCHED_CAP_REACHED"
    assert state["campaigns"][0]["status"] == "MATCHED_V1_COMPLETION"
    assert state["campaigns"][0]["matched_qty"] == "1"
    assert state["campaigns"][0]["realized"] is False


def test_outer_halts_failclosed_on_v1_policy_hold(monkeypatch, tmp_path):
    """A one-sided v1 policy hold carries residual and must halt the
    session via the residual invariant (fail-closed), not UNSAFE-status."""
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "v1-hold-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 1)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("1"))
    chosen = candidate("BTC")

    async def choose(**_kwargs):
        return chosen

    async def flat(_allowed):
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def v1_hold(_sid, _number, _state_dir, *, selected=None):
        return {
            "terminal": True,
            "status": "ONE_SIDED_RESIDUAL_POLICY_HOLD",
            "driver": "V1_POLICY",
            "selected_asset": "BTC",
            "symbol": "BTC-UP",
            "event": "BTC05M",
            "yes_qty": "1",
            "no_qty": "0",
            "maker_turnover": "0.48",
            "maker_fees": "0.01",
        }

    monkeypatch.setattr(m.selector, "select_best_candidate", choose)
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", v1_hold)

    with pytest.raises(RuntimeError, match="halted on residual"):
        asyncio.run(m.run())
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal_reason"] == "RESIDUAL_DETECTED"
    assert state["campaigns"][0]["residual_qty"] == "1"


def test_outer_treats_v1_abstain_as_benign_no_trade(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "v1-abstain-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 1)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("1"))
    monkeypatch.setattr(m, "CYCLE_PAUSE_SECONDS", 0.0)
    chosen = candidate("BTC")

    async def choose(**_kwargs):
        return chosen

    async def flat(_allowed):
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def v1_abstain(_sid, _number, _state_dir, *, selected=None):
        return {"terminal": True, "status": "V1_ABSTAIN_FLAT",
                "selected_asset": "BTC", "symbol": "BTC-UP",
                "event": "BTC05M", "yes_qty": "0", "no_qty": "0"}

    monkeypatch.setattr(m.selector, "select_best_candidate", choose)
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", v1_abstain)
    asyncio.run(m.run())    # must not raise
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["terminal_reason"] == "CAMPAIGN_CAP_REACHED"
    assert state["campaigns"][0]["status"] == "V1_ABSTAIN_FLAT"
