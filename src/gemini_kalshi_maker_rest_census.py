"""Read-only throughput census for the REST-backed maker lane.

Measures how often ETH/SOL/XRP have a dominance-safe Kalshi-maker/Gemini-taker
candidate that clears the unchanged >=3c after-fee floor with <=50ms quote skew
and <=250ms book age.  No order-entry transport is called.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_dominance_guard as dominance
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src.gemini_kalshi_aligned_watch import _discover_pairs

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_rest_census")
ASSETS = ("ETH", "SOL", "XRP")
MIN_PROFIT = D("0.03")
MAX_SKEW_MS = 50
MAX_AGE_MS = 250


@dataclass
class Stats:
    asset: str
    events: int = 0
    paired_evals: int = 0
    strict_fresh_evals: int = 0
    candidate_evals: int = 0
    candidate_episodes: int = 0
    candidate_ms: int = 0
    max_locked_profit: Decimal = D("0")
    active_since_ms: int | None = None

    def close_episode(self, now_ms: int) -> None:
        if self.active_since_ms is None:
            return
        self.candidate_ms += max(0, int(now_ms) - self.active_since_ms)
        self.active_since_ms = None


async def capture_asset(*, pair: Any, fee_meta: Any, seconds: float) -> dict[str, Any]:
    geometry = await dominance.fetch_pair_geometry(pair, pair.asset)
    if not geometry.get("authorized"):
        return {
            "asset": pair.asset,
            "window_end_utc": pair.window_end.isoformat(),
            "geometry": geometry,
            "events": 0,
            "candidate_evals": 0,
            "candidate_episodes": 0,
            "candidate_ms": 0,
            "order_entry": False,
        }
    allowed = str(geometry["dominance_safe_direction"])
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    stats_obj = base.MakerStats(asset=pair.asset)
    tasks = [
        asyncio.create_task(base._gemini_stream(pair.gemini_symbol, queue, stats_obj), name=f"census-g-{pair.asset}"),
        asyncio.create_task(rest_guard.kalshi_rest_book_stream(pair.kalshi_ticker, queue, stats_obj), name=f"census-k-{pair.asset}"),
    ]
    s = Stats(pair.asset)
    g = None
    k = None
    deadline = min(time.monotonic() + seconds, time.monotonic() + max(0.0, pair.window_end.timestamp() - time.time()))
    try:
        while time.monotonic() < deadline:
            timeout = max(0.01, min(0.5, deadline - time.monotonic()))
            try:
                kind, receive_ms, top = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            s.events += 1
            if kind == "gemini":
                g = top
            elif kind == "kalshi":
                k = top
            if g is None or k is None:
                continue
            s.paired_evals += 1
            now_ms = int(time.time() * 1000)
            skew = abs(int(g.ts_ms) - int(k.ts_ms))
            age = max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms))
            if skew > MAX_SKEW_MS or age > MAX_AGE_MS or age < 0:
                s.close_episode(now_ms)
                continue
            s.strict_fresh_evals += 1
            candidate = dominance.candidate_for_authorized_direction(
                gemini=g,
                kalshi=k,
                fee_meta=fee_meta,
                now_ms=now_ms,
                direction=allowed,
            )
            if candidate is None or candidate.locked_profit < MIN_PROFIT:
                s.close_episode(now_ms)
                continue
            auth = dominance.authorize_geometry(
                direction=candidate.direction,
                gemini_strike_value=geometry.get("gemini_strike"),
                kalshi_target_value=geometry.get("kalshi_target"),
            )
            if not auth.get("authorized"):
                s.close_episode(now_ms)
                continue
            s.candidate_evals += 1
            s.max_locked_profit = max(s.max_locked_profit, D(candidate.locked_profit))
            if s.active_since_ms is None:
                s.active_since_ms = now_ms
                s.candidate_episodes += 1
                logger.warning("MAKER_REST_CENSUS_EPISODE_OPEN %s", json.dumps({
                    "asset": pair.asset,
                    "direction": candidate.direction,
                    "maker_price": str(candidate.maker_price),
                    "hedge_price": str(candidate.hedge_price),
                    "locked_profit": str(candidate.locked_profit),
                    "queue_ahead": str(candidate.queue_ahead),
                    "quote_skew_ms": skew,
                    "book_age_ms": age,
                    "threshold_gap_bps": geometry.get("threshold_gap_bps"),
                    "classification": dominance.CLASSIFICATION,
                    "order_entry": False,
                }, separators=(",", ":")))
        s.close_episode(int(time.time() * 1000))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "asset": pair.asset,
        "window_end_utc": pair.window_end.isoformat(),
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "dominance_safe_direction": allowed,
        "threshold_gap_bps": geometry.get("threshold_gap_bps"),
        "events": s.events,
        "paired_evals": s.paired_evals,
        "strict_fresh_evals": s.strict_fresh_evals,
        "candidate_evals": s.candidate_evals,
        "candidate_episodes": s.candidate_episodes,
        "candidate_ms": s.candidate_ms,
        "max_locked_profit": str(s.max_locked_profit),
        "min_profit": str(MIN_PROFIT),
        "max_quote_skew_ms": MAX_SKEW_MS,
        "max_book_age_ms": MAX_AGE_MS,
        "classification": dominance.CLASSIFICATION,
        "counts_as_realized_pnl": False,
        "read_only": True,
        "order_entry": False,
    }


async def run(seconds: float) -> list[dict[str, Any]]:
    if seconds <= 0 or seconds > 600:
        raise ValueError("seconds must be in (0,600]")
    async with httpx.AsyncClient(timeout=20.0) as http:
        pairs = await _discover_pairs(http, 30.0)
        now = time.time()
        selected = {}
        for asset in ASSETS:
            rows = [p for p in pairs if p.asset == asset and p.window_end.timestamp() > now]
            if rows:
                selected[asset] = rows[0]
        fee_meta = {
            asset: await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[asset])
            for asset in selected
        }
    logger.warning("MAKER_REST_CENSUS_START %s", json.dumps({
        "seconds": seconds,
        "assets": sorted(selected),
        "min_profit": str(MIN_PROFIT),
        "max_quote_skew_ms": MAX_SKEW_MS,
        "max_book_age_ms": MAX_AGE_MS,
        "kalshi_transport": "EXACT_REST_DEPTH1",
        "btc_quarantined": True,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    rows = await asyncio.gather(*(
        capture_asset(pair=pair, fee_meta=fee_meta[asset], seconds=seconds)
        for asset, pair in selected.items()
    ))
    logger.warning("MAKER_REST_CENSUS_SUMMARY %s", json.dumps({
        "seconds": seconds,
        "assets": rows,
        "total_candidate_episodes": sum(int(r.get("candidate_episodes") or 0) for r in rows),
        "total_candidate_ms": sum(int(r.get("candidate_ms") or 0) for r in rows),
        "counts_as_realized_pnl": False,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds))


if __name__ == "__main__":
    main()
