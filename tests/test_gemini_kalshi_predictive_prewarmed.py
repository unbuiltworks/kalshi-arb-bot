import asyncio
import io
from types import SimpleNamespace

import pytest

from src import gemini_kalshi_predictive_prewarmed as prewarm
from src import gemini_kalshi_predictive_prewarmed_safe as safe


@pytest.mark.asyncio
async def test_inactive_prewarm_feed_is_drained_but_not_evaluated():
    pair = SimpleNamespace(
        gemini_symbol="GEMI-BTC15M2608162100-UP",
        kalshi_ticker="KXBTC15M-26AUG161700-00",
        window_end=SimpleNamespace(isoformat=lambda: "2026-08-16T21:00:00+00:00"),
    )
    stats = SimpleNamespace(dropped_events=0)
    feed = prewarm.WarmFeed(asset="BTC", pair=pair, stats=stats, boundary_ms=1000, active=False)
    pump = asyncio.create_task(safe._pump_feed_activation_safe(feed))
    try:
        top1 = SimpleNamespace(ts_ms=900)
        await feed.raw_queue.put(("gemini", 900, None, top1, 1))
        await asyncio.sleep(0)
        assert feed.gemini is top1
        assert feed.eval_queue.empty()

        feed.active = True
        top2 = SimpleNamespace(ts_ms=1001)
        await feed.raw_queue.put(("gemini", 1001, None, top2, 2))
        event = await asyncio.wait_for(feed.eval_queue.get(), timeout=0.2)
        assert event[3] is top2
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


@pytest.mark.asyncio
async def test_preboundary_backlog_cannot_leak_when_active_flag_flips():
    pair = SimpleNamespace(
        gemini_symbol="g",
        kalshi_ticker="k",
        window_end=SimpleNamespace(isoformat=lambda: "x"),
    )
    stats = SimpleNamespace(dropped_events=0)
    feed = prewarm.WarmFeed(asset="BTC", pair=pair, stats=stats, boundary_ms=1000, active=False)

    # Queue the old event first, then flip active *before* starting the pump. This is
    # the exact rollover race: an old message remains in raw_queue at activation.
    stale = SimpleNamespace(ts_ms=999)
    await feed.raw_queue.put(("gemini", 999, None, stale, 1))
    feed.active = True
    pump = asyncio.create_task(safe._pump_feed_activation_safe(feed))
    try:
        await asyncio.sleep(0)
        assert feed.gemini is stale  # market-data cache may observe it
        assert feed.eval_queue.empty()  # economics must never receive it

        fresh = SimpleNamespace(ts_ms=1000)
        await feed.raw_queue.put(("gemini", 1000, None, fresh, 2))
        event = await asyncio.wait_for(feed.eval_queue.get(), timeout=0.2)
        assert event[1] == 1000
        assert event[3] is fresh
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


def test_safe_evaluator_rejects_direct_preboundary_injection(monkeypatch):
    pair = SimpleNamespace(
        gemini_symbol="g",
        kalshi_ticker="k",
        window_end=SimpleNamespace(isoformat=lambda: "x"),
    )

    class Stats:
        evaluations = 0
        positive_evaluations = 0
        threshold_evaluations = 0
        def observe_latency(self, value):
            self.latency = value

    feed = SimpleNamespace(asset="BTC", pair=pair, stats=Stats(), boundary_ms=1000)
    monkeypatch.setattr(safe.hot, "_small_q_candidates", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not evaluate")))

    safe._evaluate_event_sequential(
        feed=feed,
        event=("gemini", 999, None, SimpleNamespace(ts_ms=999), 1),
        kalshi_fee=lambda *args: 0,
        max_quote_skew_ms=100,
        capital_limit=5,
        min_profit=0.03,
        active_episodes={},
        fh=io.StringIO(),
    )
    assert feed.stats.evaluations == 0


def test_safe_evaluator_uses_event_order_not_pump_latest(monkeypatch):
    pair = SimpleNamespace(
        gemini_symbol="g",
        kalshi_ticker="k",
        window_end=SimpleNamespace(isoformat=lambda: "x"),
    )

    class Stats:
        evaluations = 0
        positive_evaluations = 0
        threshold_evaluations = 0
        def observe_latency(self, value):
            self.latency = value

    feed = SimpleNamespace(asset="BTC", pair=pair, stats=Stats(), boundary_ms=0)
    # Simulate the warm pump already having seen a newer Gemini quote.
    feed.gemini = SimpleNamespace(ts_ms=999)
    captured = []

    def fake_candidates(g, k, **kwargs):
        captured.append((g.ts_ms, k.ts_ms))
        return []

    monkeypatch.setattr(safe.hot, "_small_q_candidates", fake_candidates)
    monkeypatch.setattr(safe.hot, "_close_missing_episodes", lambda **kwargs: None)
    monkeypatch.setattr(safe.hot, "_update_episodes", lambda **kwargs: None)

    old_g = SimpleNamespace(ts_ms=100)
    k = SimpleNamespace(ts_ms=101)
    safe._evaluate_event_sequential(
        feed=feed,
        event=("gemini", 100, None, old_g, 1),
        kalshi_fee=lambda *args: 0,
        max_quote_skew_ms=100,
        capital_limit=5,
        min_profit=0.03,
        active_episodes={},
        fh=io.StringIO(),
    )
    assert captured == []

    safe._evaluate_event_sequential(
        feed=feed,
        event=("kalshi", 101, 100, k, 1),
        kalshi_fee=lambda *args: 0,
        max_quote_skew_ms=100,
        capital_limit=5,
        min_profit=0.03,
        active_episodes={},
        fh=io.StringIO(),
    )
    assert captured == [(100, 101)]
