import pytest

from src import gemini_kalshi_gemini_flatten_fix as flatten_fix
from src.gemini_maker import live_10_canary as v1
from src.gemini_maker import live_10_canary_v2 as v2


class Event:
    symbol = "GEMI-BTC05M-X-UP"


def test_emergency_rescue_payload_is_supported_bounded_limit_ioc():
    payload = flatten_fix.build_emergency_flatten_payload(
        {"symbol": Event.symbol, "eventOutcome": "YES"},
        client_order_id="rescue-test",
    )
    assert payload == {
        "symbol": Event.symbol,
        "side": "SELL",
        "type": "LIMIT",
        "timeInForce": "IOC",
        "price": "0.01",
        "quantity": "1",
        "eventOutcome": "YES",
        "clientOrderId": "rescue-test",
    }


@pytest.mark.asyncio
async def test_complement_post_rejection_flattens_known_one_sided_fill(monkeypatch):
    async def rejected(*args, **kwargs):
        return {"hard_halt": True, "reason": "COMPLEMENT_POST_REJECTED"}

    async def flattened(**kwargs):
        return {
            "filled_quantity": "1",
            "average_fill_price": "0.44",
            "fee_paid": "0.01",
            "rescue_realized_pnl": "-0.05",
        }

    monkeypatch.setattr(v2, "_ORIGINAL_COMPLETE", rejected)
    monkeypatch.setattr(v1, "_flatten_filled", flattened)
    out = await v2._complete_missing_safe(
        object(), event=Event(), filled_outcome="YES", filled_price=v1.D("0.48"),
        filled_client_id="yes1", probe_id="p", pair_index=1,
        existing_complement_id=None, existing_complement_row=None,
        existing_complement_price=v1.D("0.49"), active_ids=set(), all_ids=[],
    )
    assert out["flattened"] is True
    assert out["hard_halt"] is False
    assert out["rescue_realized_pnl"] == v1.D("-0.05")


@pytest.mark.asyncio
async def test_rejected_or_zero_fill_rescue_remains_hard_halt(monkeypatch):
    async def rejected(*args, **kwargs):
        return {"hard_halt": True, "reason": "COMPLEMENT_POST_REJECTED"}

    async def not_filled(**kwargs):
        return {
            "filled_quantity": "0",
            "status": "REJECTED",
            "reason": "NO_LIQUIDITY",
            "rescue_realized_pnl": None,
        }

    monkeypatch.setattr(v2, "_ORIGINAL_COMPLETE", rejected)
    monkeypatch.setattr(v1, "_flatten_filled", not_filled)
    out = await v2._complete_missing_safe(
        object(), event=Event(), filled_outcome="YES", filled_price=v1.D("0.48"),
        filled_client_id="yes1", probe_id="p", pair_index=1,
        existing_complement_id=None, existing_complement_row=None,
        existing_complement_price=v1.D("0.49"), active_ids=set(), all_ids=[],
    )
    assert out["hard_halt"] is True
    assert out["reason"] == "COMPLEMENT_POST_REJECTED_FLATTEN_NOT_FULL"
    assert out["flatten"]["filled_quantity"] == "0"


@pytest.mark.asyncio
async def test_ambiguous_cancel_remains_hard_halt_without_auto_flatten(monkeypatch):
    async def ambiguous(*args, **kwargs):
        return {"hard_halt": True, "reason": "COMPLEMENT_CANCEL_AMBIGUOUS"}

    async def must_not_flatten(**kwargs):
        raise AssertionError("ambiguous inventory must not be auto-flattened")

    monkeypatch.setattr(v2, "_ORIGINAL_COMPLETE", ambiguous)
    monkeypatch.setattr(v1, "_flatten_filled", must_not_flatten)
    out = await v2._complete_missing_safe(
        object(), event=Event(), filled_outcome="YES", filled_price=v1.D("0.48"),
        filled_client_id="yes1", probe_id="p", pair_index=1,
        existing_complement_id="no1", existing_complement_row={},
        existing_complement_price=v1.D("0.49"), active_ids=set(), all_ids=[],
    )
    assert out == {"hard_halt": True, "reason": "COMPLEMENT_CANCEL_AMBIGUOUS"}


@pytest.mark.asyncio
async def test_full_flatten_is_terminal_flat_not_generic_hard_halt(monkeypatch):
    async def original(*args, **kwargs):
        return {
            "mutation": True,
            "flattened": True,
            "hard_halt": True,
            "reason": "SECOND_POST_REJECTED_AFTER_YES_FILL",
            "flatten": {"filled_quantity": "1", "rescue_realized_pnl": "-0.04"},
        }

    monkeypatch.setattr(v2, "_ORIGINAL_EXECUTE", original)
    out = await v2._execute_one_pair_safe(object())
    assert out["flattened"] is True
    assert out["hard_halt"] is False
    assert out["rescue_realized_pnl"] == v1.D("-0.04")
