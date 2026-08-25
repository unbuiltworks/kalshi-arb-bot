"""Research-only runner for MLB maker feasibility.

Normalizes ESPN team IDs to Kalshi lower-case convention and conservatively
serializes/caches Kalshi candle reads. No order entry.
"""
from __future__ import annotations

import asyncio

import src.espn_mlb_maker_feasibility as maker

_original_canonical_team = maker.ev.canonical_team
_original_ticker_candles = maker.bt._ticker_candles
_cache: dict[tuple[str, int, int], asyncio.Task] = {}
_lock = asyncio.Lock()
_last_request_at = 0.0
MIN_GAP_S = 0.25


def _canonical_lower(value):
    team = _original_canonical_team(value)
    return team.lower() if team else team


async def _throttled_candles(http, side, snapshot_ts: int, lookback_s: int = 10 * 60):
    global _last_request_at
    key = (side.ticker, int(snapshot_ts), int(lookback_s))
    task = _cache.get(key)
    if task is not None:
        return await task

    async def load():
        global _last_request_at
        async with _lock:
            loop = asyncio.get_running_loop()
            delay = MIN_GAP_S - (loop.time() - _last_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
            result = await _original_ticker_candles(http, side, snapshot_ts, lookback_s=lookback_s)
            _last_request_at = loop.time()
            return result

    task = asyncio.create_task(load())
    _cache[key] = task
    try:
        return await task
    except Exception:
        _cache.pop(key, None)
        raise


maker.ev.canonical_team = _canonical_lower
maker.bt._ticker_candles = _throttled_candles


if __name__ == "__main__":
    maker.main()
