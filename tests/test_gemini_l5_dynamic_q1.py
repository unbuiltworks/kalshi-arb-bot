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


def test_run_cycle_uses_unique_inner_session_and_reads_terminal_state(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "MIN_TOP_PAIR_EDGE", D("0.01"))
    # Register the imported ladder globals/env with monkeypatch so the test cannot
    # leak the dynamic cycle overrides into later repository tests.
    monkeypatch.setattr(m.ladder, "MIN_EDGE", m.ladder.MIN_EDGE)
    monkeypatch.setattr(m.ladder, "FIRST_WAIT", m.ladder.FIRST_WAIT)
    monkeypatch.setattr(m.ladder, "COMPLEMENT_WAIT", m.ladder.COMPLEMENT_WAIT)
    monkeypatch.setattr(m.ladder, "MIN_TTE", m.ladder.MIN_TTE)
    monkeypatch.setenv(m.ladder.LIVE_ENV, "TEST_PREVIOUS")
    monkeypatch.setenv(m.ladder.SID_ENV, "TEST_PREVIOUS")
    monkeypatch.setenv(m.ladder.ARM_ENV, "TEST_PREVIOUS")
    monkeypatch.setenv(m.ladder.STATE_ENV, "TEST_PREVIOUS")
    seen = {}
    async def fake_run():
        seen["sid"] = m.os.environ[m.ladder.SID_ENV]
        path = m.Path(m.os.environ[m.ladder.STATE_ENV])
        path.write_text(json.dumps({
            "terminal": True,
            "status": "MATCHED",
            "symbol": "ETH-UP",
            "event": "ETH05M",
            "yes_qty": "1",
            "no_qty": "1",
            "maker_turnover": "0.90",
            "maker_fees": "0.01",
        }))
    monkeypatch.setattr(m.ladder, "run", fake_run)
    out = asyncio.run(m._run_cycle("outer", 7, tmp_path))
    assert seen["sid"] == "outer-c007"
    assert out["status"] == "MATCHED"


def test_dynamic_halts_immediately_on_residual(monkeypatch, tmp_path):
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
            "yes_qty": "1",
            "no_qty": "0",
            "maker_turnover": "0.40",
            "maker_fees": "0.01",
        }
    monkeypatch.setattr(m, "account_guard", flat)
    monkeypatch.setattr(m, "_run_cycle", residual)

    with pytest.raises(RuntimeError, match="halted on residual"):
        asyncio.run(m.run())
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["terminal"] is True
    assert state["terminal_reason"] == "RESIDUAL_DETECTED"
