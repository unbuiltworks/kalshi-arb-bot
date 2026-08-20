from decimal import Decimal

import pytest

from src.gemini_kalshi_probe_transport import ExecutionResult
from src.gemini_kalshi_scaled_hedge_cushion import best_scaled_fee_aware_kalshi_hedge
from src.gemini_kalshi_scaled_probe_guard import (
    recompute_scaled_payload_locked_profit,
    validate_scaled_probe_payloads,
)
from src.gemini_kalshi_scaled_staged_probe_transport import submit_scaled_gemini_first_probe

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


G5 = {
    "symbol": "GEMI-BTC15M-X-UP",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "FOK",
    "price": "0.25",
    "quantity": "5",
    "eventOutcome": "NO",
    "clientOrderId": "scale-g",
}
K5 = {
    "ticker": "KXBTC15M-X",
    "client_order_id": "scale-k",
    "side": "bid",
    "count": "5.00",
    "price": "0.60",
    "time_in_force": "fill_or_kill",
    "self_trade_prevention_type": "taker_at_cross",
    "post_only": False,
    "cancel_order_on_pause": True,
    "reduce_only": False,
    "subaccount": 0,
    "exchange_index": 0,
}
EDGE5 = recompute_scaled_payload_locked_profit(G5, K5, kalshi_fee_multiplier=D("1"))


class FakeGemini:
    def __init__(self, primary_fill: str, *, primary_status: str | None = None, order=None):
        self.primary_fill = D(primary_fill)
        self.primary_status = primary_status
        self.order = order if order is not None else []
        self.buy_payloads = []
        self.flatten_payloads = []

    async def place_fok(self, payload, terminal_timeout=None):
        if str(payload.get("side")) == "SELL":
            self.order.append("gemini_flatten")
            self.flatten_payloads.append(dict(payload))
            return _result("gemini", str(payload.get("quantity")), status="FILLED")
        self.order.append("gemini")
        self.buy_payloads.append(dict(payload))
        return _result("gemini", str(self.primary_fill), status=self.primary_status)


class FakeKalshi:
    def __init__(self, fill: str, *, status: str | None = None, order=None):
        self.fill = D(fill)
        self.status = status
        self.order = order if order is not None else []
        self.payloads = []

    async def place(self, payload):
        self.order.append("kalshi")
        self.payloads.append(dict(payload))
        return _result("kalshi", str(self.fill), status=self.status)


def test_q5_exact_economics_preserve_three_cents_per_contract():
    assert EDGE5 / D("5") >= D("0.03")
    recomputed = validate_scaled_probe_payloads(
        G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        required_quantity=D("5"),
    )
    assert recomputed == EDGE5


def test_q5_rejects_low_per_contract_profit_even_when_total_is_positive():
    g = dict(G5); k = dict(K5)
    g["price"] = "0.48"; k["price"] = "0.48"
    edge = recompute_scaled_payload_locked_profit(g, k, kalshi_fee_multiplier=D("1"))
    assert edge > 0
    with pytest.raises(ValueError, match="per contract below floor"):
        validate_scaled_probe_payloads(
            g, k,
            modeled_locked_profit=edge,
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            required_quantity=D("5"),
        )


def test_q5_cushion_spends_at_most_two_cents_of_price_and_keeps_total_floor():
    cushion = best_scaled_fee_aware_kalshi_hedge(
        G5, K5,
        kalshi_fee_multiplier=D("1"),
        min_profit_per_contract=D("0.03"),
        max_cushion_cents=2,
    )
    assert 0 <= cushion.cushion_cents <= 2
    assert cushion.cushioned_locked_profit >= D("0.15")
    assert cushion.cushioned_yes_price >= D("0.60")


@pytest.mark.asyncio
async def test_q5_primary_zero_fill_never_sends_kalshi_and_is_benign(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("0", primary_status="CANCELED", order=order)
    kalshi = FakeKalshi("5", order=order)
    result = await submit_scaled_gemini_first_probe(
        gemini, kalshi, G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        required_quantity=D("5"), cli_live=True,
    )
    assert result["outcome"] == "PRIMARY_NOT_FILLED"
    assert result["hard_halt_required"] is False
    assert order == ["gemini"]


@pytest.mark.asyncio
async def test_q5_both_full_fills_are_gemini_then_cushioned_kalshi(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("5", order=order)
    kalshi = FakeKalshi("5", order=order)
    result = await submit_scaled_gemini_first_probe(
        gemini, kalshi, G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        required_quantity=D("5"), cli_live=True,
    )
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert order == ["gemini", "kalshi"]
    assert D(kalshi.payloads[0]["price"]) >= D(K5["price"])
    assert D(result["hedge_recomputed_locked_profit_dollars"]) >= D("0.15")


@pytest.mark.asyncio
async def test_q5_primary_partial_fill_flattens_exact_primary_quantity_and_never_hedges(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("2", order=order)
    kalshi = FakeKalshi("5", order=order)
    result = await submit_scaled_gemini_first_probe(
        gemini, kalshi, G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        required_quantity=D("5"), cli_live=True,
    )
    assert result["outcome"] == "PRIMARY_PARTIAL_FILL"
    assert result["hard_halt_required"] is True
    assert order == ["gemini", "gemini_flatten"]
    assert gemini.flatten_payloads[0]["quantity"] == "2"


@pytest.mark.asyncio
async def test_q5_kalshi_zero_fill_flattens_all_five_and_halts(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("5", order=order)
    kalshi = FakeKalshi("0", status="CANCELED", order=order)
    result = await submit_scaled_gemini_first_probe(
        gemini, kalshi, G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        required_quantity=D("5"), cli_live=True,
    )
    assert result["outcome"] == "HEDGE_NOT_FILLED"
    assert result["hard_halt_required"] is True
    assert order == ["gemini", "kalshi", "gemini_flatten"]
    assert gemini.flatten_payloads[0]["quantity"] == "5"


@pytest.mark.asyncio
async def test_q5_kalshi_partial_three_flattens_only_two_unhedged(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("5", order=order)
    kalshi = FakeKalshi("3", order=order)
    result = await submit_scaled_gemini_first_probe(
        gemini, kalshi, G5, K5,
        modeled_locked_profit=EDGE5,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        required_quantity=D("5"), cli_live=True,
    )
    assert result["outcome"] == "HEDGE_PARTIAL_FILL"
    assert result["unhedged_quantity_before_flatten"] == "2"
    assert result["hard_halt_required"] is True
    assert gemini.flatten_payloads[0]["quantity"] == "2"


@pytest.mark.asyncio
async def test_q5_economics_failure_occurs_before_first_send(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    gemini = FakeGemini("5", order=order)
    kalshi = FakeKalshi("5", order=order)
    with pytest.raises(ValueError, match="do not match modeled profit"):
        await submit_scaled_gemini_first_probe(
            gemini, kalshi, G5, K5,
            modeled_locked_profit=EDGE5 + D("0.01"),
            kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
            required_quantity=D("5"), cli_live=True,
        )
    assert order == []
