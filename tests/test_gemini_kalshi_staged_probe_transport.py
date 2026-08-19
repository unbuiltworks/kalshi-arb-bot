from decimal import Decimal

import pytest

from src.gemini_kalshi_live_probe_guard import (
    build_leg_payloads,
    recompute_payload_locked_profit,
)
from src.gemini_kalshi_probe_transport import ExecutionResult
from src.gemini_kalshi_staged_probe_transport import submit_gemini_first_probe

D = Decimal


def _result(venue: str, filled: str, *, status: str | None = None, reason: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        venue=venue,
        client_order_id=f"{venue}-id",
        filled_quantity=filled,
        average_fill_price="0.50" if D(filled) > 0 else None,
        fee_paid="0.01" if D(filled) > 0 else None,
        status=status or ("FILLED" if D(filled) > 0 else "CANCELED"),
        reason=reason,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=3,
        request_latency_ms=0.001,
        raw_request_status=200,
    )


class FakeGemini:
    def __init__(self, filled: str, *, status: str | None = None, reason: str | None = None, order=None):
        self.filled = filled
        self.status = status
        self.reason = reason
        self.placed = 0
        self.flattened = 0
        self.order = order

    async def place_fok(self, payload):
        self.placed += 1
        if self.order is not None:
            self.order.append("gemini")
        return _result("gemini", self.filled, status=self.status, reason=self.reason)

    async def emergency_flatten_market(self, payload):
        self.flattened += 1
        if self.order is not None:
            self.order.append("gemini_flatten")
        return _result("gemini", "1")


class FakeKalshi:
    def __init__(self, filled: str, *, status: str | None = None, order=None):
        self.filled = filled
        self.status = status
        self.placed = 0
        self.order = order

    async def place(self, payload):
        self.placed += 1
        if self.order is not None:
            self.order.append("kalshi")
        return _result("kalshi", self.filled, status=self.status)


G, K = build_leg_payloads(
    direction="GEMINI_YES_KALSHI_NO",
    gemini_symbol="GEMI-BTC15M-X-UP",
    kalshi_ticker="KXBTC15M-X",
    gemini_yes_bid=D("0.41"),
    gemini_yes_ask=D("0.42"),
    kalshi_yes_bid=D("0.51"),
    kalshi_yes_ask=D("0.52"),
)
EDGE = recompute_payload_locked_profit(G, K, kalshi_fee_multiplier=D("1"))


@pytest.mark.asyncio
async def test_staged_probe_disabled_without_two_factor_opt_in(monkeypatch):
    monkeypatch.delenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", raising=False)
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(PermissionError):
        await submit_gemini_first_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=EDGE,
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_primary_no_fill_never_submits_kalshi_and_is_benign(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("0", status="CANCELED", reason="FillOrKillWouldNotFill", order=order)
    kalshi = FakeKalshi("1", order=order)
    result = await submit_gemini_first_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "PRIMARY_NOT_FILLED"
    assert result["hard_halt_required"] is False
    assert result["kalshi"] is None
    assert gemini.placed == 1
    assert kalshi.placed == 0
    assert order == ["gemini"]


@pytest.mark.asyncio
async def test_both_filled_is_strictly_gemini_then_kalshi(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("1", order=order)
    kalshi = FakeKalshi("1", order=order)
    result = await submit_gemini_first_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert order == ["gemini", "kalshi"]


@pytest.mark.asyncio
async def test_gemini_fill_then_kalshi_miss_flattens_gemini_and_halts(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("1", order=order)
    kalshi = FakeKalshi("0", status="CANCELED", order=order)
    result = await submit_gemini_first_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "ONE_LEG_FILLED"
    assert result["hard_halt_required"] is True
    assert gemini.flattened == 1
    assert order == ["gemini", "kalshi", "gemini_flatten"]


@pytest.mark.asyncio
async def test_ambiguous_primary_never_submits_kalshi_and_halts(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("0", status="UNKNOWN")
    kalshi = FakeKalshi("1")
    result = await submit_gemini_first_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "AMBIGUOUS_PRIMARY_SUBMISSION"
    assert result["hard_halt_required"] is True
    assert result["kalshi"] is None
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_ambiguous_kalshi_after_gemini_fill_halts_without_guessing(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("0", status="UNKNOWN")
    result = await submit_gemini_first_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "AMBIGUOUS_HEDGE_SUBMISSION"
    assert result["hard_halt_required"] is True
    assert gemini.flattened == 0


@pytest.mark.asyncio
async def test_payload_economics_still_fail_before_any_send(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(ValueError):
        await submit_gemini_first_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=EDGE + D("0.01"),
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0
