from decimal import Decimal

import pytest

from src.gemini_kalshi_probe_transport import ExecutionResult
from src.gemini_kalshi_q10_gemini_flatten import build_q10_emergency_flatten_payload
from src.gemini_kalshi_q10_hedge_cushion import best_q10_fee_aware_kalshi_hedge
from src.gemini_kalshi_q10_probe_guard import (
    q10_economics,
    q10_reservation_requirements,
    validate_q10_probe_payloads,
)
from src.gemini_kalshi_q10_staged_probe_transport import submit_q10_gemini_first_probe

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


G10 = {
    "symbol": "GEMI-BTC15M-Q10-UP",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "FOK",
    "price": "0.25",
    "quantity": "10",
    "eventOutcome": "NO",
    "clientOrderId": "q10-g",
}
K10 = {
    "ticker": "KXBTC15M-Q10",
    "client_order_id": "q10-k",
    "side": "bid",
    "count": "10.00",
    "price": "0.60",
    "time_in_force": "fill_or_kill",
}
E10 = q10_economics(G10, K10, kalshi_fee_multiplier=D("1"))


class FakeGemini:
    def __init__(self, primary_fill: str, *, primary_status: str | None = None, order=None):
        self.primary_fill = D(primary_fill)
        self.primary_status = primary_status
        self.order = order if order is not None else []
        self.flatten_payloads = []

    async def place_fok(self, payload, terminal_timeout=None):
        if str(payload.get("side")) == "SELL":
            self.order.append("gemini_flatten")
            self.flatten_payloads.append(dict(payload))
            return _result("gemini", str(payload.get("quantity")), status="FILLED")
        self.order.append("gemini")
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


def _reservations():
    cushion = best_q10_fee_aware_kalshi_hedge(
        G10, K10, kalshi_fee_multiplier=D("1"), max_cushion_cents=2,
    )
    return q10_reservation_requirements(
        G10, cushion.payload, kalshi_fee_multiplier=D("1"),
    )


def test_q10_exact_economics_and_three_cent_floor():
    assert E10.profit_per_contract >= D("0.03")
    validated = validate_q10_probe_payloads(
        G10, K10,
        modeled_locked_profit=E10.locked_profit,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=50,
    )
    assert validated.locked_profit == E10.locked_profit


def test_q10_is_exact_quantity_not_q5_or_q25():
    for q in ("5", "25"):
        g = dict(G10); k = dict(K10)
        g["quantity"] = q; k["count"] = f"{q}.00"
        with pytest.raises(ValueError, match="exactly 10"):
            q10_economics(g, k, kalshi_fee_multiplier=D("1"))


def test_q10_cushion_never_exceeds_two_cents_and_keeps_30c_total_floor():
    cushion = best_q10_fee_aware_kalshi_hedge(
        G10, K10, kalshi_fee_multiplier=D("1"), max_cushion_cents=2,
    )
    assert 0 <= cushion.cushion_cents <= 2
    assert cushion.cushioned_locked_profit >= D("0.30")
    assert cushion.cushioned_yes_price <= D("0.62")


def test_q10_reservations_are_venue_specific_actual_cash():
    reserve = _reservations()
    assert reserve["gemini"] > 0
    assert reserve["kalshi"] > 0
    assert reserve["pair_total"] == reserve["gemini"] + reserve["kalshi"]
    assert reserve["pair_total"] < D("10")


def test_q10_flatten_builder_accepts_exact_partial_or_full_but_not_over_ten():
    p3 = build_q10_emergency_flatten_payload(G10, quantity=D("3"))
    p10 = build_q10_emergency_flatten_payload(G10, quantity=D("10"))
    assert p3["quantity"] == "3"
    assert p10["quantity"] == "1E+1"
    assert p10["side"] == "SELL" and p10["timeInForce"] == "IOC" and p10["price"] == "0.01"
    with pytest.raises(ValueError, match="<= 10"):
        build_q10_emergency_flatten_payload(G10, quantity=D("11"))


@pytest.mark.asyncio
async def test_insufficient_kalshi_reservation_fails_before_gemini_send(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    r = _reservations()
    gemini = FakeGemini("10", order=order)
    kalshi = FakeKalshi("10", order=order)
    with pytest.raises(ValueError, match="kalshi reservation insufficient"):
        await submit_q10_gemini_first_probe(
            gemini, kalshi, G10, K10,
            modeled_locked_profit=E10.locked_profit,
            kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
            reserved_cash_by_venue={"gemini": r["gemini"], "kalshi": r["kalshi"] - D("0.01")},
            cli_live=True,
        )
    assert order == []


@pytest.mark.asyncio
async def test_q10_primary_zero_fill_never_sends_kalshi(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    r = _reservations()
    gemini = FakeGemini("0", primary_status="CANCELED", order=order)
    kalshi = FakeKalshi("10", order=order)
    result = await submit_q10_gemini_first_probe(
        gemini, kalshi, G10, K10,
        modeled_locked_profit=E10.locked_profit,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        reserved_cash_by_venue=r, cli_live=True,
    )
    assert result["outcome"] == "PRIMARY_NOT_FILLED"
    assert result["hard_halt_required"] is False
    assert order == ["gemini"]


@pytest.mark.asyncio
async def test_q10_both_full_fills_are_gemini_then_kalshi(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    r = _reservations()
    gemini = FakeGemini("10", order=order)
    kalshi = FakeKalshi("10", order=order)
    result = await submit_q10_gemini_first_probe(
        gemini, kalshi, G10, K10,
        modeled_locked_profit=E10.locked_profit,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        reserved_cash_by_venue=r, cli_live=True,
    )
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert order == ["gemini", "kalshi"]
    assert result["capital_reservation"]["required_pair_total"] == str(r["pair_total"])


@pytest.mark.asyncio
async def test_q10_primary_partial_three_flattens_exact_three_and_never_hedges(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    r = _reservations()
    gemini = FakeGemini("3", order=order)
    kalshi = FakeKalshi("10", order=order)
    result = await submit_q10_gemini_first_probe(
        gemini, kalshi, G10, K10,
        modeled_locked_profit=E10.locked_profit,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        reserved_cash_by_venue=r, cli_live=True,
    )
    assert result["outcome"] == "PRIMARY_PARTIAL_FILL"
    assert result["hard_halt_required"] is True
    assert order == ["gemini", "gemini_flatten"]
    assert gemini.flatten_payloads[0]["quantity"] == "3"


@pytest.mark.asyncio
async def test_q10_kalshi_partial_six_flattens_only_four(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    order = []
    r = _reservations()
    gemini = FakeGemini("10", order=order)
    kalshi = FakeKalshi("6", order=order)
    result = await submit_q10_gemini_first_probe(
        gemini, kalshi, G10, K10,
        modeled_locked_profit=E10.locked_profit,
        kalshi_fee_multiplier=D("1"), quote_skew_ms=10,
        reserved_cash_by_venue=r, cli_live=True,
    )
    assert result["outcome"] == "HEDGE_PARTIAL_FILL"
    assert result["unhedged_quantity_before_flatten"] == "4"
    assert result["hard_halt_required"] is True
    assert order == ["gemini", "kalshi", "gemini_flatten"]
    assert gemini.flatten_payloads[0]["quantity"] == "4"
