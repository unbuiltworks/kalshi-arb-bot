from __future__ import annotations

import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import src.gemini_kalshi_probe_transport_fractional_safe_v2 as mod
from src.gemini_kalshi_probe_transport import ExecutionResult


def result(venue: str, *, status: str, qty: str, client: str = "cid") -> ExecutionResult:
    return ExecutionResult(
        venue=venue,
        client_order_id=client,
        filled_quantity=qty,
        average_fill_price="0.40" if D(qty) > 0 else None,
        fee_paid="0.01" if D(qty) > 0 else None,
        status=status,
        reason=None,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=None,
        request_latency_ms=1.0,
        ack_latency_ms=1.0,
        raw_request_status=200,
        reconciled=False,
    )


class Resp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class KClient:
    def __init__(self, market_status=200):
        self.market_status = market_status

    async def get(self, path, params=None):
        if "/orderbook" in path:
            return Resp(200, {"orderbook_fp": {"yes_dollars": [["0.30", "5"]], "no_dollars": [["0.60", "5"]]}})
        return Resp(self.market_status, {"market": {"ticker": "KTEST", "status": "active"}})


class Kalshi:
    def __init__(self, results, market_status=200):
        self.client = KClient(market_status)
        self.results = list(results)
        self.calls = []

    async def place(self, payload):
        self.calls.append(dict(payload))
        return self.results.pop(0)


class Gemini:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def place_fok(self, payload):
        self.calls.append(dict(payload))
        return self.results.pop(0)


GP = {
    "symbol": "GTEST",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "FOK",
    "price": "0.40",
    "quantity": "1",
    "eventOutcome": "YES",
    "clientOrderId": "g-test",
}
KP = {
    "ticker": "KTEST",
    "client_order_id": "k-test",
    "side": "ask",
    "count": "1.00",
    "price": "0.60",
    "time_in_force": "fill_or_kill",
    "self_trade_prevention_type": "taker_at_cross",
    "post_only": False,
    "cancel_order_on_pause": True,
    "reduce_only": False,
    "subaccount": 0,
    "exchange_index": 0,
}


def install_common(monkeypatch):
    monkeypatch.setattr(mod, "live_probe_authorized", lambda x: True)
    monkeypatch.setattr(mod, "validate_exact_probe_payloads", lambda *a, **k: D("0.03"))


def run(coro):
    return asyncio.run(coro)


def test_market_not_found_blocks_before_any_mutation(monkeypatch):
    install_common(monkeypatch)
    kalshi = Kalshi([], market_status=404)
    gemini = Gemini([])
    out = run(mod.submit_dual_probe_fractional_safe_v2(
        gemini, kalshi, dict(GP), dict(KP),
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"), quote_skew_ms=10, cli_live=True,
    ))
    assert out["outcome"] == "NEITHER_FILLED"
    assert out["kalshi"]["reason"] == "MARKET_HTTP_404"
    assert kalshi.calls == []
    assert gemini.calls == []


def test_kalshi_zero_fill_never_touches_gemini(monkeypatch):
    install_common(monkeypatch)
    kalshi = Kalshi([result("kalshi", status="CANCELED", qty="0")])
    gemini = Gemini([])
    out = run(mod.submit_dual_probe_fractional_safe_v2(
        gemini, kalshi, dict(GP), dict(KP),
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"), quote_skew_ms=10, cli_live=True,
    ))
    assert out["outcome"] == "NEITHER_FILLED"
    assert len(kalshi.calls) == 1
    assert gemini.calls == []


def test_kalshi_fill_then_gemini_limit_lost_flattens_kalshi_without_gemini_send(monkeypatch):
    install_common(monkeypatch)
    kalshi = Kalshi([
        result("kalshi", status="FILLED", qty="1"),
        result("kalshi", status="FILLED", qty="1", client="flatten-k"),
    ])
    gemini = Gemini([])
    async def pos(*a, **k): return D("0")
    async def book(*a, **k):
        return SimpleNamespace(yes_ask=D("0.41"), yes_ask_size=D("5"), yes_bid=D("0.39"), yes_bid_size=D("5"))
    monkeypatch.setattr(mod, "_gemini_position_qty", pos)
    monkeypatch.setattr(mod, "gemini_book_ticker_once", book)
    out = run(mod.submit_dual_probe_fractional_safe_v2(
        gemini, kalshi, dict(GP), dict(KP),
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"), quote_skew_ms=10, cli_live=True,
    ))
    assert out["outcome"] == "KALSHI_FILLED_GEMINI_LIMIT_LOST"
    assert out["hard_halt_required"] is True
    assert len(kalshi.calls) == 2
    assert kalshi.calls[1]["reduce_only"] is True
    assert gemini.calls == []


def test_both_fill_in_kalshi_then_gemini_order(monkeypatch):
    install_common(monkeypatch)
    kalshi = Kalshi([result("kalshi", status="FILLED", qty="1")])
    gemini = Gemini([result("gemini", status="FILLED", qty="1")])
    async def pos(*a, **k): return D("0")
    async def book(*a, **k):
        return SimpleNamespace(yes_ask=D("0.40"), yes_ask_size=D("5"), yes_bid=D("0.39"), yes_bid_size=D("5"))
    monkeypatch.setattr(mod, "_gemini_position_qty", pos)
    monkeypatch.setattr(mod, "gemini_book_ticker_once", book)
    out = run(mod.submit_dual_probe_fractional_safe_v2(
        gemini, kalshi, dict(GP), dict(KP),
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"), quote_skew_ms=10, cli_live=True,
    ))
    assert out["outcome"] == "BOTH_FILLED"
    assert out["hard_halt_required"] is False
    assert len(kalshi.calls) == 1
    assert len(gemini.calls) == 1


def test_unknown_gemini_with_zero_position_delta_flattens_kalshi(monkeypatch):
    install_common(monkeypatch)
    kalshi = Kalshi([
        result("kalshi", status="FILLED", qty="1"),
        result("kalshi", status="FILLED", qty="1", client="flatten-k"),
    ])
    gemini = Gemini([result("gemini", status="UNKNOWN", qty="0")])
    values = iter([D("0"), D("0")])
    async def pos(*a, **k): return next(values)
    async def book(*a, **k):
        return SimpleNamespace(yes_ask=D("0.40"), yes_ask_size=D("5"), yes_bid=D("0.39"), yes_bid_size=D("5"))
    monkeypatch.setattr(mod, "_gemini_position_qty", pos)
    monkeypatch.setattr(mod, "gemini_book_ticker_once", book)
    out = run(mod.submit_dual_probe_fractional_safe_v2(
        gemini, kalshi, dict(GP), dict(KP),
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"), quote_skew_ms=10, cli_live=True,
    ))
    assert out["outcome"] == "ONE_LEG_FILLED"
    assert out["hard_halt_required"] is True
    assert len(gemini.calls) == 1
    assert len(kalshi.calls) == 2


def test_no_market_order_in_gemini_flatten(monkeypatch):
    install_common(monkeypatch)
    gemini = Gemini([result("gemini", status="FILLED", qty="0.25")])
    async def book(*a, **k):
        return SimpleNamespace(yes_ask=D("0.41"), yes_ask_size=D("5"), yes_bid=D("0.39"), yes_bid_size=D("5"))
    monkeypatch.setattr(mod, "gemini_book_ticker_once", book)
    run(mod._flatten_gemini_limit_fok(gemini, dict(GP), D("0.25")))
    payload = gemini.calls[0]
    assert payload["type"] == "LIMIT"
    assert payload["timeInForce"] == "FOK"
    assert payload["side"] == "SELL"
    assert payload["quantity"] == "0.25"
