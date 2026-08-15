from decimal import Decimal

import pytest

from src.gemini_kalshi_probe_transport import ExecutionResult, submit_dual_probe

D = Decimal


def _result(venue: str, filled: str) -> ExecutionResult:
    return ExecutionResult(
        venue=venue,
        client_order_id=f"{venue}-id",
        filled_quantity=filled,
        average_fill_price="0.50" if D(filled) > 0 else None,
        fee_paid="0.01" if D(filled) > 0 else None,
        status="FILLED" if D(filled) > 0 else "CANCELED",
        reason=None,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=3,
        request_latency_ms=0.001,
        raw_request_status=200,
    )


class FakeGemini:
    def __init__(self, filled: str):
        self.filled = filled
        self.placed = 0
        self.flattened = 0

    async def place_fok(self, payload):
        self.placed += 1
        return _result("gemini", self.filled)

    async def emergency_flatten_market(self, payload):
        self.flattened += 1
        return _result("gemini", "1")


class FakeKalshi:
    def __init__(self, filled: str):
        self.filled = filled
        self.placed = 0
        self.flattened = 0

    async def place(self, payload):
        self.placed += 1
        return _result("kalshi", self.filled)

    async def emergency_flatten(self, payload):
        self.flattened += 1
        return _result("kalshi", "1")


G = {
    "symbol": "GEMI-BTC15M-X-UP",
    "quantity": "1",
    "timeInForce": "FOK",
    "clientOrderId": "g",
}
K = {
    "ticker": "KXBTC15M-X",
    "count": "1.00",
    "time_in_force": "fill_or_kill",
    "client_order_id": "k",
}


@pytest.mark.asyncio
async def test_dual_probe_is_disabled_without_two_factor_opt_in(monkeypatch):
    monkeypatch.delenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", raising=False)
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(PermissionError):
        await submit_dual_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=D("0.03"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_dual_probe_rejects_bad_edge_before_send(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(ValueError):
        await submit_dual_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=D("0.02"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_one_leg_fill_triggers_emergency_flatten(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("0")
    result = await submit_dual_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=D("0.03"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "ONE_LEG_FILLED"
    assert gemini.flattened == 1
    assert kalshi.flattened == 0


@pytest.mark.asyncio
async def test_both_filled_needs_no_flatten(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    result = await submit_dual_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=D("0.03"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "BOTH_FILLED"
    assert gemini.flattened == 0
    assert kalshi.flattened == 0
