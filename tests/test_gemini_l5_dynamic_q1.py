import asyncio
import json
from decimal import Decimal as D

import pytest

from src import gemini_l5_dynamic_q1 as m


class Response:
    status_code = 200
    def __init__(self, body):
        self._body = body
    def json(self):
        return self._body


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


def test_run_cycle_uses_unique_inner_session_and_pair_config(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "MIN_TOP_PAIR_EDGE", D("0.01"))
    seen = {}
    async def fake_run(*, min_edge, first_wait, complement_wait, min_tte):
        seen["sid"] = m.os.environ[m.pair.SID_ENV]
        seen["min_edge"] = min_edge
        seen["first_wait"] = first_wait
        path = m.Path(m.os.environ[m.pair.STATE_ENV])
        path.write_text(json.dumps({
            "terminal": True,
            "status": "MATCHED",
            "symbol": "ETH-UP",
            "event": "ETH05M",
            "backoff": "0.01",
            "entry_edge": "0.02",
            "yes_qty": "1",
            "no_qty": "1",
            "maker_turnover": "0.96",
            "maker_fees": "0.02",
        }))
    monkeypatch.setattr(m.pair, "run", fake_run)
    out = asyncio.run(m._run_cycle("outer", 7, tmp_path))
    assert seen["sid"] == "outer-c007"
    assert seen["min_edge"] == D("0.01")
    assert seen["first_wait"] == m.FIRST_WAIT_SECONDS
    assert out["status"] == "MATCHED"
    assert out["backoff"] == "0.01"


def test_outer_accepts_dynamic_completion_match_and_records_rescue(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "armed", lambda: True)
    monkeypatch.setenv(m.SID_ENV, "dynamic-rescue-test")
    monkeypatch.setenv(m.STATE_ENV, str(tmp_path / "session.json"))
    monkeypatch.setattr(m, "MAX_SESSION_SECONDS", 60.0)
    monkeypatch.setattr(m, "MAX_CAMPAIGNS", 1)
    monkeypatch.setattr(m, "MAX_MATCHED_Q", D("12"))
    monkeypatch.setattr(m, "CYCLE_PAUSE_SECONDS", 0.0)
    seen_allowed = []

    async def flat(allowed):
        seen_allowed.append(set(allowed))
        return {"active_orders": 0, "positions": [], "mutation": False}

    async def matched(_sid, _number, _state_dir):
        return {
            "terminal": True,
            "status": "MATCHED_DYNAMIC_COMPLETION",
            "rescue_status": "MATCHED",
            "completion_reserve_ticks": 1,
            "completion_reprices": 1,
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

    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", matched)
    asyncio.run(m.run())

    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
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

    async def flat(_allowed):
        return {"active_orders": 0, "positions": [], "mutation": False}
    async def residual(_sid, _number, _state_dir):
        return {
            "terminal": True,
            "status": "ONE_SIDED_RESIDUAL",
            "symbol": "ETH-UP",
            "event": "ETH05M",
            "backoff": "0.01",
            "entry_edge": "0.02",
            "yes_qty": "1",
            "no_qty": "0",
            "maker_turnover": "0.47",
            "maker_fees": "0.01",
        }
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", residual)

    with pytest.raises(RuntimeError, match="halted on residual"):
        asyncio.run(m.run())
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["terminal_reason"] == "RESIDUAL_DETECTED"
    assert state["campaigns"][0]["residual_qty"] == "1"
