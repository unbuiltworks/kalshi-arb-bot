import asyncio
from decimal import Decimal

import pytest

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata
from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe
from src import gemini_kalshi_maker_live_probe_q1_safe_v2 as final

D = Decimal


def book(venue, market, yes_bid, yes_bid_size, yes_ask, yes_ask_size, ts=1_800_000_000_000):
    return TopOfBook(
        venue=venue,
        market_id=market,
        ts_ms=ts,
        yes_bid=D(yes_bid),
        yes_bid_size=D(yes_bid_size),
        yes_ask=D(yes_ask),
        yes_ask_size=D(yes_ask_size),
    )


def meta(*, changes=()):
    return KalshiSeriesFeeMetadata(
        series_ticker="KXXRP15M",
        fee_type="quadratic",
        fee_multiplier="1",
        last_updated_ts="",
        scheduled_changes=tuple(changes),
    )


def test_final_selector_refuses_safe_price_below_existing_best_bid():
    # Gemini NO is expensive enough that a 3c-safe Kalshi YES maker price must
    # sit below the displayed Kalshi best bid. The first live probe refuses it.
    g = book("gemini", "g", "0.45", "5", "0.47", "5")  # NO ask = 0.55
    k = book("kalshi", "k", "0.55", "5", "0.70", "5")
    original = final._ORIGINAL_CANDIDATE(gemini=g, kalshi=k, fee_meta=meta(), now_ms=g.ts_ms)
    if original is not None and original.maker_price < original.best_bid:
        assert final.candidate_from_books(gemini=g, kalshi=k, fee_meta=meta(), now_ms=g.ts_ms) is None


def test_final_selector_accepts_at_or_above_best_bid_candidate():
    g = book("gemini", "g", "0.70", "5", "0.72", "5")  # NO ask = 0.30
    k = book("kalshi", "k", "0.55", "5", "0.66", "5")
    candidate = final.candidate_from_books(gemini=g, kalshi=k, fee_meta=meta(), now_ms=g.ts_ms)
    assert candidate is not None
    assert candidate.maker_price >= candidate.best_bid
    assert candidate.maker_price < candidate.best_ask
    assert candidate.locked_profit >= base.TARGET_PROFIT


def test_fee_gate_refuses_scheduled_change():
    with pytest.raises(RuntimeError):
        safe._fee_mapping_gate(meta(changes=({"series_ticker": "KXXRP15M"},)))


def test_fee_gate_accepts_current_quadratic_no_maker_fee():
    safe._fee_mapping_gate(meta())


def test_partial_fill_is_flattened_and_hard_halted(monkeypatch):
    async def fake_cancel(*args, **kwargs):
        return {"ok": True}, [], D("0.5")

    async def fake_flatten(*args, **kwargs):
        return {"status": "FILLED", "filled_quantity": "0.5"}

    monkeypatch.setattr(safe, "_cancel_and_reconcile", fake_cancel)
    monkeypatch.setattr(base, "_flatten_kalshi_qty", fake_flatten)

    result = asyncio.run(safe._handle_confirmed_fill(
        gemini=object(),
        kalshi=object(),
        candidate=object(),
        g=None,
        pair=object(),
        k_payload={"side": "bid"},
        order_id="order-1",
        run_id="run-1",
        observed_qty=D("0.5"),
        is_maker=True,
    ))
    assert result["outcome"] == "PARTIAL_MAKER_FILL_FLATTENED"
    assert result["hard_halt"] is True
    assert result["reconciled_fill_qty"] == "0.5"
