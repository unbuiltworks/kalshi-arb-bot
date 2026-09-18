"""Structural and frame tests for the bounded Gemini L5 WS canary."""
import asyncio
import json
from pathlib import Path

import pytest

from src import gemini_l5_ws_canary as W


def preflight():
    return {"ok": True, "instrument": "GEMI-BTC05M2609180200-UP",
            "limit_price": "0.48", "quantity": "0.01",
            "restability": {"restable": True}}


def test_exact_sanitized_frame_is_proven_ws_dialect():
    assert W.sanitized_ws_frame(preflight()) == {
        "id": "<request-id>", "method": "order.place",
        "params": {"symbol": "GEMI-BTC05M2609180200-UP", "side": "BUY",
                   "type": "LIMIT", "timeInForce": "MOC", "price": "0.48",
                   "quantity": "0.01", "eventOutcome": "YES",
                   "clientOrderId": "<owner-auth>-l5ws"}}


def test_envelope_is_exactly_one_btc5m_maker_order():
    assert W.QTY == "0.01" and W.MAX_ORDERS == 1 and W.MAX_RESIDUAL == "0.01"
    p = W.order_params(preflight(), "owner")
    assert p["type"] == "LIMIT" and p["timeInForce"] == "MOC"
    assert p["symbol"].startswith("GEMI-BTC05M")


def test_refuses_nonresting_or_wrong_quantity():
    x = preflight(); x["restability"]["restable"] = False
    with pytest.raises(ValueError): W.order_params(x, "owner")
    x = preflight(); x["quantity"] = "1"
    with pytest.raises(ValueError): W.order_params(x, "owner")


def test_durable_latch_is_one_shot(tmp_path):
    p = W._consume_latch(tmp_path, "OWNER-TEST", {"x": 1})
    assert p.exists()
    with pytest.raises(FileExistsError):
        W._consume_latch(tmp_path, "OWNER-TEST", {"x": 1})


def test_latch_refuses_missing_owner_authorization(tmp_path):
    with pytest.raises(PermissionError):
        W._consume_latch(tmp_path, "", {})


@pytest.mark.asyncio
async def test_no_order_request_when_latch_already_consumed(tmp_path, monkeypatch):
    W._consume_latch(tmp_path, "OWNER-TEST", {"prior": True})
    called = {"connect": 0}
    async def boom(self):
        called["connect"] += 1
        raise AssertionError("network must remain unreachable after consumed latch")
    monkeypatch.setattr(W.GeminiMakerSession, "connect", boom)
    with pytest.raises(FileExistsError):
        await W.submit_once(authorization_id="OWNER-TEST", latch_root=str(tmp_path),
                            fresh_preflight=preflight())
    assert called["connect"] == 0
