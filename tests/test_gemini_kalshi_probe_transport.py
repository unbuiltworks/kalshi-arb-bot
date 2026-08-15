from decimal import Decimal

import pytest

import src.gemini_kalshi_probe_transport as transport
from src.gemini_kalshi_live_probe_guard import (
    build_leg_payloads,
    recompute_payload_locked_profit,
)
from src.gemini_kalshi_probe_transport import (
    ExecutionResult,
    KALSHI_PROD_REST,
    submit_dual_probe,
)

D = Decimal


def _result(venue: str, filled: str, *, status: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        venue=venue,
        client_order_id=f"{venue}-id",
        filled_quantity=filled,
        average_fill_price="0.50" if D(filled) > 0 else None,
        fee_paid="0.01" if D(filled) > 0 else None,
        status=status or ("FILLED" if D(filled) > 0 else "CANCELED"),
        reason=None,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=3,
        request_latency_ms=0.001,
        raw_request_status=200,
    )


class FakeGemini:
    def __init__(self, filled: str, *, status: str | None = None):
        self.filled = filled
        self.status = status
        self.placed = 0
        self.flattened = 0

    async def place_fok(self, payload):
        self.placed += 1
        return _result("gemini", self.filled, status=self.status)

    async def emergency_flatten_market(self, payload):
        self.flattened += 1
        return _result("gemini", "1")


class FakeKalshi:
    def __init__(self, filled: str, *, status: str | None = None):
        self.filled = filled
        self.status = status
        self.placed = 0
        self.flattened = 0

    async def place(self, payload):
        self.placed += 1
        return _result("kalshi", self.filled, status=self.status)

    async def emergency_flatten(self, payload):
        self.flattened += 1
        return _result("kalshi", "1")


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
async def test_dual_probe_is_disabled_without_two_factor_opt_in(monkeypatch):
    monkeypatch.delenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", raising=False)
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(PermissionError):
        await submit_dual_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=EDGE,
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_dual_probe_rejects_stale_or_wrong_modeled_edge_before_send(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    with pytest.raises(ValueError):
        await submit_dual_probe(
            gemini, kalshi, G, K,
            modeled_locked_profit=EDGE + D("0.01"),
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            cli_live=True,
        )
    assert gemini.placed == 0
    assert kalshi.placed == 0


@pytest.mark.asyncio
async def test_one_leg_fill_triggers_emergency_flatten_and_halt(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("0")
    result = await submit_dual_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "ONE_LEG_FILLED"
    assert result["hard_halt_required"] is True
    assert gemini.flattened == 1
    assert kalshi.flattened == 0


@pytest.mark.asyncio
async def test_ambiguous_submission_halts_without_guessing_or_flattening(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("0", status="UNKNOWN")
    kalshi = FakeKalshi("1")
    result = await submit_dual_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "AMBIGUOUS_SUBMISSION"
    assert result["hard_halt_required"] is True
    assert gemini.flattened == 0
    assert kalshi.flattened == 0


@pytest.mark.asyncio
async def test_both_filled_needs_no_flatten(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    gemini = FakeGemini("1")
    kalshi = FakeKalshi("1")
    result = await submit_dual_probe(
        gemini, kalshi, G, K,
        modeled_locked_profit=EDGE,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        cli_live=True,
    )
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert gemini.flattened == 0
    assert kalshi.flattened == 0


def test_default_kalshi_executor_is_pinned_to_production(monkeypatch):
    seen = {}

    class StubClient:
        def __init__(self, *, base_url=None, **kwargs):
            seen["base_url"] = base_url
            self.base_url = base_url

    monkeypatch.setattr(transport, "KalshiClient", StubClient)
    executor = transport.KalshiV2Executor()
    assert executor.client.base_url == KALSHI_PROD_REST
    assert seen["base_url"] == KALSHI_PROD_REST
