import asyncio
from types import SimpleNamespace

import pytest

from src.gemini_kalshi_gemini_flatten_fix import (
    EMERGENCY_MIN_PRICE,
    build_emergency_flatten_payload,
    _emergency_flatten_limit_ioc,
)


def test_build_emergency_flatten_uses_limit_ioc_at_min_price():
    payload = build_emergency_flatten_payload(
        {"symbol": "GEMI-BTC15M2608200300-UP", "eventOutcome": "YES"},
        client_order_id="flatten-test",
    )
    assert payload == {
        "symbol": "GEMI-BTC15M2608200300-UP",
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "IOC",
        "price": EMERGENCY_MIN_PRICE,
        "quantity": "1",
        "eventOutcome": "YES",
        "clientOrderId": "flatten-test",
    }
    assert payload["type"] != "MARKET"


def test_build_emergency_flatten_fails_closed_on_bad_outcome():
    with pytest.raises(ValueError):
        build_emergency_flatten_payload({"symbol": "GEMI-X", "eventOutcome": "MAYBE"})


def test_emergency_flatten_sends_exact_limit_ioc_payload():
    calls = []

    class FakeSession:
        async def place_fok(self, payload, *, terminal_timeout):
            calls.append((payload, terminal_timeout))
            return SimpleNamespace(filled_quantity="1", status="CANCELED")

    result = asyncio.run(
        _emergency_flatten_limit_ioc(
            FakeSession(),
            {"symbol": "GEMI-BTC15M2608200300-UP", "eventOutcome": "YES"},
            timeout=1.25,
        )
    )
    assert result.filled_quantity == "1"
    assert len(calls) == 1
    payload, timeout = calls[0]
    assert payload["side"] == "SELL"
    assert payload["type"] == "LIMIT"
    assert payload["timeInForce"] == "IOC"
    assert payload["price"] == "0.01"
    assert timeout == 1.25
