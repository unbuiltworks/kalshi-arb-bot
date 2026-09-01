"""Read-only AWS smoke for the persistent Gemini q10 prediction-book hot path.

Public market data only. No credentials. No order entry.
"""
from __future__ import annotations

import asyncio
import json
import time

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_prediction_book_cache import GeminiTopCache


async def run() -> None:
    event = await base.discover_btc5m()
    if event is None:
        raise RuntimeError("no BTC5m event available for hot-path smoke")
    cache = GeminiTopCache(event.symbol, stale_ms=750)
    stop = asyncio.Event()
    task = asyncio.create_task(cache.run(stop))
    try:
        t0 = time.perf_counter_ns()
        first = await cache.wait_fresh(timeout=4.0, max_age_ms=500)
        warm_ms = (time.perf_counter_ns() - t0) / 1_000_000
        await asyncio.sleep(0.20)
        t1 = time.perf_counter_ns()
        hot = await cache.wait_fresh(timeout=0.25, max_age_ms=500)
        hot_read_ms = (time.perf_counter_ns() - t1) / 1_000_000
        print("GEMINI_Q10_HOT_PATH_SMOKE=" + json.dumps({
            "classification": "READ_ONLY_PUBLIC_HOT_PATH_SMOKE",
            "order_entry": False,
            "credentials_loaded": False,
            "ticker": event.ticker,
            "symbol": event.symbol,
            "seconds_to_expiry": event.seconds_to_expiry,
            "warmup_ms": round(warm_ms, 3),
            "hot_read_ms": round(hot_read_ms, 3),
            "first_book_age_ms": round(first.age_ms, 3),
            "hot_book_age_ms": round(hot.age_ms, 3),
            "first_sequence": first.sequence,
            "hot_sequence": hot.sequence,
            "connected": cache.connected,
            "last_error": cache.last_error,
            "bid": str(hot.bid),
            "ask": str(hot.ask),
        }, separators=(",", ":"), default=str))
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
