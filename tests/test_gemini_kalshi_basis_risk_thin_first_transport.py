from decimal import Decimal

import pytest

from src import gemini_kalshi_basis_risk_thin_first_transport as thin
from src import gemini_kalshi_probe_transport as transport

D = Decimal


def result(venue, status, qty="0", reason=None):
    return transport.ExecutionResult(
        venue=venue,
        client_order_id=f"{venue}-id",
        filled_quantity=qty,
        average_fill_price="0.50" if qty == "1" else None,
        fee_paid="0.01" if qty == "1" else None,
        status=status,
        reason=reason,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=None,
        request_latency_ms=0.1,
    )


class FakeGemini:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def place_fok(self, payload):
        self.calls += 1
        return self.response


class FakeKalshi:
    def __init__(self, response, flatten_response=None):
        self.response = response
        self.flatten_response = flatten_response or result("kalshi", "FILLED", "1")
        self.place_calls = 0
        self.flatten_calls = 0

    async def place(self, payload):
        self.place_calls += 1
        return self.response

    async def emergency_flatten(self, payload):
        self.flatten_calls += 1
        return self.flatten_response


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    monkeypatch.setattr(transport, "live_probe_authorized", lambda cli: bool(cli))
    monkeypatch.setattr(transport, "validate_exact_probe_payloads", lambda *a, **k: D("0.03"))


def payloads():
    return (
        {"clientOrderId": "g-id"},
        {"client_order_id": "k-id"},
    )


@pytest.mark.asyncio
async def test_kalshi_reject_means_gemini_is_never_submitted():
    g = FakeGemini(result("gemini", "FILLED", "1"))
    k = FakeKalshi(result("kalshi", "REJECTED", "0", "FOK_NOT_FILLED"))
    gp, kp = payloads()
    out = await thin.submit_kalshi_first_probe(
        g, k, gp, kp,
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20, cli_live=True,
    )
    assert out["outcome"] == "NEITHER_FILLED"
    assert g.calls == 0
    assert k.place_calls == 1
    assert out["gemini"]["status"] == "NOT_SUBMITTED"
    assert out["hard_halt_required"] is False


@pytest.mark.asyncio
async def test_gemini_only_submits_after_confirmed_kalshi_fill():
    g = FakeGemini(result("gemini", "FILLED", "1"))
    k = FakeKalshi(result("kalshi", "FILLED", "1"))
    gp, kp = payloads()
    out = await thin.submit_kalshi_first_probe(
        g, k, gp, kp,
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20, cli_live=True,
    )
    assert out["outcome"] == "BOTH_FILLED"
    assert k.place_calls == 1
    assert g.calls == 1
    assert k.flatten_calls == 0
    assert out["execution_sequence"] == "KALSHI_THEN_GEMINI"


@pytest.mark.asyncio
async def test_gemini_second_reject_triggers_kalshi_flatten_and_hard_halt():
    g = FakeGemini(result("gemini", "CANCELED", "0", "FillOrKillWouldNotFill"))
    k = FakeKalshi(
        result("kalshi", "FILLED", "1"),
        flatten_response=result("kalshi", "FILLED", "1"),
    )
    gp, kp = payloads()
    out = await thin.submit_kalshi_first_probe(
        g, k, gp, kp,
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20, cli_live=True,
    )
    assert out["outcome"] == "ONE_LEG_FILLED"
    assert g.calls == 1
    assert k.flatten_calls == 1
    assert out["flatten_recovered"] is True
    assert out["hard_halt_required"] is True


@pytest.mark.asyncio
async def test_ambiguous_kalshi_first_never_submits_gemini():
    g = FakeGemini(result("gemini", "FILLED", "1"))
    k = FakeKalshi(result("kalshi", "UNKNOWN", "0", "AMBIGUOUS"))
    gp, kp = payloads()
    out = await thin.submit_kalshi_first_probe(
        g, k, gp, kp,
        modeled_locked_profit=D("0.03"), kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20, cli_live=True,
    )
    assert out["outcome"] == "AMBIGUOUS_KALSHI_FIRST"
    assert g.calls == 0
    assert out["hard_halt_required"] is True
