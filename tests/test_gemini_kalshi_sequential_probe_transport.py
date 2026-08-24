from decimal import Decimal

import pytest

from src import gemini_kalshi_sequential_probe_transport as seq
from src.gemini_kalshi_probe_transport import ExecutionResult

D = Decimal


def _result(venue: str, *, filled: str, status: str = "FILLED", reason=None) -> ExecutionResult:
    return ExecutionResult(
        venue=venue,
        client_order_id=f"{venue}-id",
        filled_quantity=filled,
        average_fill_price="0.40" if D(filled) > 0 else None,
        fee_paid="0.01" if D(filled) > 0 else None,
        status=status,
        reason=reason,
        local_send_ns=1,
        local_done_ns=2,
        exchange_ts_ns=None,
        request_latency_ms=0.1,
    )


class FakeKalshi:
    def __init__(self, first, flatten=None):
        self.first = first
        self.flatten_result = flatten or _result("kalshi", filled="1")
        self.place_calls = 0
        self.flatten_calls = 0

    async def place(self, payload):
        self.place_calls += 1
        return self.first

    async def emergency_flatten(self, payload):
        self.flatten_calls += 1
        return self.flatten_result


class FakeGemini:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def place_fok(self, payload):
        self.calls += 1
        return self.result


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    monkeypatch.setattr(seq.base, "live_probe_authorized", lambda cli_live: bool(cli_live))
    monkeypatch.setattr(seq, "validate_exact_probe_payloads", lambda *args, **kwargs: D("0.03"))


def _kwargs():
    return dict(
        gemini_payload={"clientOrderId": "g"},
        kalshi_payload={"client_order_id": "k"},
        modeled_locked_profit=D("0.03"),
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20,
        cli_live=True,
    )


@pytest.mark.asyncio
async def test_kalshi_miss_never_submits_gemini():
    kalshi = FakeKalshi(_result("kalshi", filled="0", status="CANCELED", reason="FOK_NOT_FILLED"))
    gemini = FakeGemini(_result("gemini", filled="1"))
    out = await seq.submit_kalshi_first_probe(gemini, kalshi, **_kwargs())
    assert out["outcome"] == "NEITHER_FILLED"
    assert gemini.calls == 0
    assert kalshi.flatten_calls == 0
    assert out["hard_halt_required"] is False


@pytest.mark.asyncio
async def test_both_fill_after_kalshi_confirmation():
    kalshi = FakeKalshi(_result("kalshi", filled="1"))
    gemini = FakeGemini(_result("gemini", filled="1"))
    out = await seq.submit_kalshi_first_probe(gemini, kalshi, **_kwargs())
    assert out["outcome"] == "BOTH_FILLED"
    assert kalshi.place_calls == 1
    assert gemini.calls == 1
    assert kalshi.flatten_calls == 0
    assert out["execution_order"] == "KALSHI_THEN_GEMINI"


@pytest.mark.asyncio
async def test_confirmed_gemini_miss_flattens_kalshi_and_halts():
    kalshi = FakeKalshi(_result("kalshi", filled="1"), flatten=_result("kalshi", filled="1"))
    gemini = FakeGemini(_result("gemini", filled="0", status="CANCELED", reason="FOK_NOT_FILLED"))
    out = await seq.submit_kalshi_first_probe(gemini, kalshi, **_kwargs())
    assert out["outcome"] == "ONE_LEG_FILLED_FLATTENED"
    assert gemini.calls == 1
    assert kalshi.flatten_calls == 1
    assert out["hard_halt_required"] is True


@pytest.mark.asyncio
async def test_ambiguous_kalshi_halts_before_gemini():
    kalshi = FakeKalshi(_result("kalshi", filled="0", status="UNKNOWN", reason="AMBIGUOUS"))
    gemini = FakeGemini(_result("gemini", filled="1"))
    out = await seq.submit_kalshi_first_probe(gemini, kalshi, **_kwargs())
    assert out["outcome"] == "AMBIGUOUS_FIRST_LEG"
    assert gemini.calls == 0
    assert out["hard_halt_required"] is True


@pytest.mark.asyncio
async def test_ambiguous_gemini_does_not_guess_flatten():
    kalshi = FakeKalshi(_result("kalshi", filled="1"))
    gemini = FakeGemini(_result("gemini", filled="0", status="UNKNOWN", reason="AMBIGUOUS"))
    out = await seq.submit_kalshi_first_probe(gemini, kalshi, **_kwargs())
    assert out["outcome"] == "AMBIGUOUS_SECOND_LEG"
    assert kalshi.flatten_calls == 0
    assert out["hard_halt_required"] is True


@pytest.mark.asyncio
async def test_live_authorization_still_required(monkeypatch):
    monkeypatch.setattr(seq.base, "live_probe_authorized", lambda cli_live: False)
    with pytest.raises(PermissionError):
        await seq.submit_kalshi_first_probe(
            FakeGemini(_result("gemini", filled="1")),
            FakeKalshi(_result("kalshi", filled="1")),
            **_kwargs(),
        )
