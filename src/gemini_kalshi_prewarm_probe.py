"""Read-only predictive rollover prewarm probe.

Keeps the proven HOT scanner untouched and separately tests whether the next
15-minute Gemini/Kalshi contracts can be subscribed before the quarter-hour
boundary. No REST market discovery and no order-entry code are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from src.gemini_kalshi_predictive_hot import predicted_pair, _next_window_end
from src import gemini_kalshi_multi_ws_hot as hot

logger = logging.getLogger("kalshi_arb.gemini_kalshi_prewarm_probe")


async def probe_asset(asset: str, boundary_ts: float, lead_s: float, timeout_after_s: float) -> None:
    # The post-boundary contract ends one quarter-hour after boundary. Asking the
    # deterministic identity helper just after boundary returns that target pair.
    pair = predicted_pair(asset, now_ts=boundary_ts + 0.001)
    start_wall = boundary_ts - lead_s
    await asyncio.sleep(max(0.0, start_wall - time.time()))

    start_ms = int(time.time() * 1000)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    stats = hot.AssetStats(asset)
    tasks = [
        asyncio.create_task(hot._gemini_stream(pair.gemini_symbol, queue, stats), name=f"prewarm-gemini-{asset}"),
        asyncio.create_task(hot._kalshi_stream(pair.kalshi_ticker, queue, stats), name=f"prewarm-kalshi-{asset}"),
    ]

    first: dict[str, int] = {}
    deadline = boundary_ts + timeout_after_s
    logger.info("CRYPTO_HOT_PREWARM_START %s", json.dumps({
        "asset": asset,
        "boundary_utc_ms": int(boundary_ts * 1000),
        "subscription_start_ms": start_ms,
        "start_relative_boundary_ms": start_ms - int(boundary_ts * 1000),
        "target_window_end_utc": pair.window_end.isoformat(),
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "lead_s": lead_s,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    try:
        while time.time() < deadline:
            timeout = max(0.01, min(0.5, deadline - time.time()))
            try:
                venue, receive_ms, _exchange_ms, _top, _mono_ns = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            first.setdefault(venue, receive_ms)
            if "gemini" in first and "kalshi" in first:
                ready_ms = max(first.values())
                boundary_ms = int(boundary_ts * 1000)
                logger.info("CRYPTO_HOT_PREWARM_BOTH_BOOKS %s", json.dumps({
                    "asset": asset,
                    "gemini_first_seen_ms": first["gemini"],
                    "kalshi_first_seen_ms": first["kalshi"],
                    "both_books_ready_ms": ready_ms,
                    "ready_relative_boundary_ms": ready_ms - boundary_ms,
                    "subscription_start_ms": start_ms,
                    "start_relative_boundary_ms": start_ms - boundary_ms,
                    "target_window_end_utc": pair.window_end.isoformat(),
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "gemini_reconnects": stats.gemini_reconnects,
                    "kalshi_reconnects": stats.kalshi_reconnects,
                    "kalshi_sequence_gaps": stats.kalshi_sequence_gaps,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
                return
        logger.info("CRYPTO_HOT_PREWARM_TIMEOUT %s", json.dumps({
            "asset": asset,
            "first_seen": first,
            "target_window_end_utc": pair.window_end.isoformat(),
            "timeout_after_s": timeout_after_s,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(assets: list[str], lead_s: float, timeout_after_s: float, rollovers: int = 0) -> None:
    """Benchmark consecutive rollovers; ``rollovers=0`` means run continuously."""
    completed = 0
    while rollovers <= 0 or completed < rollovers:
        boundary = _next_window_end()
        boundary_ts = boundary.timestamp()
        logger.info("CRYPTO_HOT_PREWARM_PLAN %s", json.dumps({
            "boundary_utc": boundary.isoformat(),
            "lead_s": lead_s,
            "assets": assets,
            "rollover_index": completed + 1,
            "rollovers_requested": rollovers,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        await asyncio.gather(*(probe_asset(a, boundary_ts, lead_s, timeout_after_s) for a in assets))
        completed += 1
        # probe_asset normally returns shortly after the boundary. This tiny guard
        # prevents a same-boundary repeat if all subscriptions fail unusually early.
        await asyncio.sleep(max(0.05, boundary_ts + 0.05 - time.time()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--lead-s", type=float, default=1.0)
    parser.add_argument("--timeout-after-s", type=float, default=8.0)
    parser.add_argument(
        "--rollovers",
        type=int,
        default=0,
        help="Number of quarter-hour rollovers to benchmark; 0 runs continuously",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run([a.upper() for a in args.assets], args.lead_s, args.timeout_after_s, args.rollovers))


if __name__ == "__main__":
    main()
