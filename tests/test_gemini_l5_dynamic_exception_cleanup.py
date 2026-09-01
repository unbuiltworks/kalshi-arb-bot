import asyncio
import json
from pathlib import Path

import pytest

from src import gemini_l5_dynamic_exception_cleanup as m


class Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
    def json(self):
        return self._body


def test_cleanup_cancels_only_exact_cycle_ids(monkeypatch, tmp_path: Path):
    path = tmp_path / "cycle-001.json"
    path.write_text(json.dumps({
        "terminal": False,
        "symbol": "GEMI-ETH05M-X-UP",
        "orders": [
            {"id": "yes-1", "side": "YES", "price": "0.40"},
            {"id": "no-1", "side": "NO", "price": "0.50"},
        ],
    }))
    calls = []
    active_reads = 0

    async def fake_request(method, endpoint, *, params, timeout):
        nonlocal active_reads
        calls.append((endpoint, dict(params)))
        if endpoint.endswith("/orders/active"):
            active_reads += 1
            if active_reads == 1:
                return Response({"orders": [
                    {"clientOrderId": "yes-1", "orderId": "101"},
                    {"clientOrderId": "no-1", "orderId": "102"},
                ]})
            return Response({"orders": []})
        if endpoint.endswith("/order/cancel"):
            return Response({"ok": True})
        raise AssertionError(endpoint)

    monkeypatch.setattr(m, "private_rest_request", fake_request)
    monkeypatch.setattr(m.asyncio, "sleep", lambda *_: asyncio.sleep(0))

    out = asyncio.run(m.cleanup_cycle(path, error="RuntimeError: boom"))
    assert out["cleanup_verified"] is True
    cancelled_ids = {str(c["order_id"]) for c in out["cancelled"]}
    assert cancelled_ids == {"101", "102"}
    saved = json.loads(path.read_text())
    assert saved["status"] == "EXCEPTION_CLEANUP_VERIFIED"
    assert saved["terminal"] is True


def test_cleanup_refuses_unknown_active_order(monkeypatch, tmp_path: Path):
    path = tmp_path / "cycle-002.json"
    path.write_text(json.dumps({
        "terminal": False,
        "symbol": "GEMI-ETH05M-X-UP",
        "orders": [{"id": "yes-1", "side": "YES", "price": "0.40"}],
    }))

    async def fake_request(method, endpoint, *, params, timeout):
        if endpoint.endswith("/orders/active"):
            return Response({"orders": [{"clientOrderId": "some-other-owner", "orderId": "999"}]})
        raise AssertionError("cleanup must refuse before any cancel")

    monkeypatch.setattr(m, "private_rest_request", fake_request)
    with pytest.raises(RuntimeError, match="unknown active prediction orders"):
        asyncio.run(m.cleanup_cycle(path, error="RuntimeError: boom"))


def test_cleanup_with_no_cycle_state_is_verified_noop(tmp_path: Path):
    out = asyncio.run(m.cleanup_cycle(tmp_path / "missing.json", error="x"))
    assert out["cycle_state_present"] is False
    assert out["cleanup_verified"] is True
