import pytest

from src.gemini_maker import live_10_canary as v1
from src.gemini_maker import live_10_canary_v2 as v2


class Event:
    symbol = "GEMI-BTC05M-X-UP"


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
