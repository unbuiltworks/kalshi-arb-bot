"""Research-only compatibility runner for the ESPN/DraftKings MLB close backtest.

Adapters only; strategy parameters are unchanged:
- normalize ESPN canonical team IDs to Kalshi's lower-case convention;
- cache each Kalshi ticker/cutoff candle request;
- serialize historical candle reads at a conservative pace to avoid 429s.

No order entry is imported or enabled.
"""
from __future__ import annotations

import asyncio

import src.espn_mlb_closing_ev_backtest as backtest

_original_canonical_team = backtest.canonical_team
_original_ticker_candles = backtest.bt._ticker_candles
_candle_cache: dict[tuple[str, int, int], asyncio.Task] = {}
_candle_lock = asyncio.Lock()
_last_candle_request_at = 0.0
MIN_CANDLE_REQUEST_GAP_S = 0.25


def _canonical_team_lower(value):
    team = _original_canonical_team(value)
    return team.lower() if team else team


async def _throttled_ticker_candles(http, side, snapshot_ts: int, lookback_s: int = 10 * 60):
    global _last_candle_request_at
    key = (side.ticker, int(snapshot_ts), int(lookback_s))
    existing = _candle_cache.get(key)
    if existing is not None:
        return await existing

    async def _load():
        global _last_candle_request_at
        async with _candle_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = MIN_CANDLE_REQUEST_GAP_S - (now - _last_candle_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
            result = await _original_ticker_candles(http, side, snapshot_ts, lookback_s=lookback_s)
            _last_candle_request_at = loop.time()
            return result

    task = asyncio.create_task(_load())
    _candle_cache[key] = task
    try:
        return await task
    except Exception:
        _candle_cache.pop(key, None)
        raise


backtest.canonical_team = _canonical_team_lower
backtest.bt._ticker_candles = _throttled_ticker_candles
backtest.KALSHI_CONCURRENCY = 4


if __name__ == "__main__":
    backtest.main()
