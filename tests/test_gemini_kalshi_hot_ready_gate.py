import asyncio
from types import SimpleNamespace

import pytest

from src import gemini_kalshi_hot_ready_gate as gate


@pytest.mark.asyncio
async def test_ready_gate_waits_for_both_books_and_seeds_evaluator(monkeypatch):
    feed = SimpleNamespace(
        asset="BTC",
        pair=SimpleNamespace(
            gemini_symbol="g",
            kalshi_ticker="k",
            window_end=SimpleNamespace(isoformat=lambda: "2026-08-16T21:15:00+00:00"),
        ),
        gemini=None,
        kalshi=None,
        latest_k_exchange_ts=None,
        gemini_first_seen_ms=1,
        kalshi_first_seen_ms=None,
        active=False,
    )

    async def fake_start_next_at(**kwargs):
        async def populate():
            await asyncio.sleep(0.005)
            feed.gemini = SimpleNamespace(ts_ms=10)
            feed.kalshi = SimpleNamespace(ts_ms=11)
            feed.latest_k_exchange_ts = 9
            feed.kalshi_first_seen_ms = 11
        asyncio.create_task(populate())
        return feed

    monkeypatch.setattr(gate, "_original_start_next_at", fake_start_next_at)
    monkeypatch.setattr(gate.hot.logger, "info", lambda *args: None)

    result = await gate._start_next_at_ready(asset="BTC")

    assert result is feed
    assert feed.active is False
    assert feed.eval_gemini is feed.gemini
    assert feed.eval_kalshi is feed.kalshi
    assert feed.eval_latest_k_exchange_ts == 9
    assert feed.ready_gate_passed_ms > 0
