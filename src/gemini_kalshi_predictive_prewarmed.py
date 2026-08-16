"""Continuous double-buffered read-only Gemini/Kalshi 15-minute HOT scanner.

The proven predictive scanner waits until the old capture finishes before opening the
next pair. Live rollover measurement showed that a one-second predictive prewarm gets
both next-window books usable ~130-150 ms after the boundary, while sequential startup
lands around 1.3-1.5 s.

This module preserves the validated economics/read-only path but double-buffers market
data:
- current window remains actively evaluated until the exact quarter-hour boundary;
- next deterministic Gemini/Kalshi sockets start ``prewarm_lead_s`` before boundary;
- prewarmed data is drained but never evaluated before its window starts;
- at boundary the evaluator switches to the warm feed and the old sockets are closed.

No order-entry imports or trade authorization are added.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
from pathlib import Path
import time
from typing import Any

from src import gemini_kalshi_predictive_hot as predictive

hot = predictive.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_predictive_prewarmed")


@dataclass
class WarmFeed:
    asset: str
    pair: Any
    stats: Any
    boundary_ms: int
    active: bool
    raw_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=hot.MAX_QUEUE))
    eval_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=hot.MAX_QUEUE))
    stream_tasks: list[asyncio.Task] = field(default_factory=list)
    pump_task: asyncio.Task | None = None
    gemini: Any | None = None
    kalshi: Any | None = None
    latest_k_exchange_ts: int | None = None
    gemini_first_seen_ms: int | None = None
    kalshi_first_seen_ms: int | None = None
    both_ready_logged: bool = False


async def _pump_feed(feed: WarmFeed) -> None:
    while True:
        event = await feed.raw_queue.get()
        venue, receive_ms, exchange_ms, top, _trigger_mono_ns = event
        if venue == "gemini":
            feed.gemini = top
            if feed.gemini_first_seen_ms is None:
                feed.gemini_first_seen_ms = receive_ms
        else:
            feed.kalshi = top
            feed.latest_k_exchange_ts = exchange_ms
            if feed.kalshi_first_seen_ms is None:
                feed.kalshi_first_seen_ms = receive_ms

        if (
            not feed.both_ready_logged
            and feed.gemini_first_seen_ms is not None
            and feed.kalshi_first_seen_ms is not None
        ):
            ready_ms = max(feed.gemini_first_seen_ms, feed.kalshi_first_seen_ms)
            feed.both_ready_logged = True
            logger.info(
                "CRYPTO_HOT_PREWARM_READY %s",
                json.dumps({
                    "asset": feed.asset,
                    "gemini_symbol": feed.pair.gemini_symbol,
                    "kalshi_ticker": feed.pair.kalshi_ticker,
                    "window_end_utc": feed.pair.window_end.isoformat(),
                    "gemini_first_seen_ms": feed.gemini_first_seen_ms,
                    "kalshi_first_seen_ms": feed.kalshi_first_seen_ms,
                    "both_books_ready_ms": ready_ms,
                    "ready_relative_boundary_ms": ready_ms - feed.boundary_ms,
                    "was_active_when_ready": feed.active,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )

        # The essential safety property: next-window messages are consumed so their
        # socket queues stay healthy, but they cannot reach economics evaluation until
        # the quarter-hour switch flips ``active``.
        if feed.active:
            try:
                feed.eval_queue.put_nowait(event)
            except asyncio.QueueFull:
                feed.stats.dropped_events += 1


async def _start_feed(*, asset: str, pair: Any, stats: Any, active: bool) -> WarmFeed:
    boundary_ms = int((pair.window_end.timestamp() - 900.0) * 1000)
    feed = WarmFeed(
        asset=asset,
        pair=pair,
        stats=stats,
        boundary_ms=boundary_ms,
        active=active,
    )
    feed.stream_tasks = [
        asyncio.create_task(
            hot._gemini_stream(pair.gemini_symbol, feed.raw_queue, stats),
            name=f"hot-gemini-{asset}-{int(pair.window_end.timestamp())}",
        ),
        asyncio.create_task(
            hot._kalshi_stream(pair.kalshi_ticker, feed.raw_queue, stats),
            name=f"hot-kalshi-{asset}-{int(pair.window_end.timestamp())}",
        ),
    ]
    feed.pump_task = asyncio.create_task(
        _pump_feed(feed),
        name=f"hot-pump-{asset}-{int(pair.window_end.timestamp())}",
    )
    logger.info(
        "CRYPTO_HOT_DOUBLE_BUFFER_FEED_START %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "active": active,
            "start_relative_window_start_ms": int(time.time() * 1000) - boundary_ms,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _stop_feed(feed: WarmFeed) -> None:
    feed.active = False
    for task in feed.stream_tasks:
        task.cancel()
    if feed.pump_task is not None:
        feed.pump_task.cancel()
    await asyncio.gather(*feed.stream_tasks, return_exceptions=True)
    if feed.pump_task is not None:
        await asyncio.gather(feed.pump_task, return_exceptions=True)


async def _start_next_at(
    *,
    asset: str,
    stats: Any,
    boundary_ts: float,
    prewarm_lead_s: float,
) -> WarmFeed:
    await asyncio.sleep(max(0.0, boundary_ts - prewarm_lead_s - time.time()))
    next_pair = predictive.predicted_pair(asset, now_ts=boundary_ts + 0.001)
    feed = await _start_feed(asset=asset, pair=next_pair, stats=stats, active=False)
    logger.info(
        "CRYPTO_HOT_PREWARM_ARMED %s",
        json.dumps({
            "asset": asset,
            "boundary_utc_ms": int(boundary_ts * 1000),
            "prewarm_lead_s": prewarm_lead_s,
            "next_window_end_utc": next_pair.window_end.isoformat(),
            "gemini_symbol": next_pair.gemini_symbol,
            "kalshi_ticker": next_pair.kalshi_ticker,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


def _evaluate_event(
    *,
    feed: WarmFeed,
    event: tuple,
    kalshi_fee,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    active_episodes: dict[str, Any],
    fh,
) -> None:
    venue, receive_ms, _exchange_ms, _top, trigger_mono_ns = event
    g, k = feed.gemini, feed.kalshi
    if g is None or k is None:
        return

    stats = feed.stats
    stats.evaluations += 1
    all_small = hot._small_q_candidates(
        g,
        k,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
    )
    if all_small:
        stats.positive_evaluations += 1

    best_by_direction: dict[str, tuple[int, Any]] = {}
    for q, candidate in all_small:
        prior = best_by_direction.get(candidate.direction)
        if prior is None or candidate.locked_profit > prior[1].locked_profit:
            best_by_direction[candidate.direction] = (q, candidate)
    qualifying = {
        direction: item
        for direction, item in best_by_direction.items()
        if item[1].locked_profit >= min_profit
    }
    if qualifying:
        stats.threshold_evaluations += 1

    eval_done_ns = time.perf_counter_ns()
    stats.observe_latency((eval_done_ns - trigger_mono_ns) / 1_000_000)
    hot._close_missing_episodes(
        active=active_episodes,
        qualifying=qualifying,
        now_ms=receive_ms,
        asset=feed.asset,
        pair=feed.pair,
    )
    hot._update_episodes(
        active=active_episodes,
        qualifying=qualifying,
        now_ms=receive_ms,
        asset=feed.asset,
        pair=feed.pair,
        trigger_venue=venue,
    )

    row = {
        "asset": feed.asset,
        "window_end_utc": feed.pair.window_end.isoformat(),
        "event_venue": venue,
        "receive_ts_ms": receive_ms,
        "gemini_receive_ts_ms": g.ts_ms,
        "kalshi_receive_ts_ms": k.ts_ms,
        "kalshi_exchange_ts_ms": feed.latest_k_exchange_ts,
        "kalshi_exchange_to_receive_ms": (
            k.ts_ms - feed.latest_k_exchange_ts
            if feed.latest_k_exchange_ts is not None
            else None
        ),
        "gemini_symbol": feed.pair.gemini_symbol,
        "kalshi_ticker": feed.pair.kalshi_ticker,
        "small_q_candidates": [hot._candidate_payload(q, c) for q, c in all_small],
        "trigger_to_eval_ms": round((eval_done_ns - trigger_mono_ns) / 1_000_000, 3),
        "rollover_mode": "PREDICTIVE_DOUBLE_BUFFER",
        "settlement_status": hot.SETTLEMENT_STATUS,
        "read_only": True,
        "order_entry": False,
    }
    fh.write(json.dumps(row, separators=(",", ":")) + "\n")


async def collect_asset_prewarmed(
    *,
    http,
    discovery,
    asset: str,
    output_dir: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    fee_meta,
    overall_deadline: float | None,
    prewarm_lead_s: float = 1.0,
) -> None:
    del http, discovery
    stats = hot.AssetStats(asset)
    kalshi_fee = fee_meta.taker_fee_fn()
    pair = predictive.predicted_pair(asset)
    current = await _start_feed(asset=asset, pair=pair, stats=stats, active=True)
    stats.windows += 1
    output = output_dir / f"crypto_hot_{asset.lower()}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    active_episodes: dict[str, Any] = {}
    next_heartbeat = time.monotonic() + hot.HEARTBEAT_S

    try:
        with output.open("a", encoding="utf-8") as fh:
            while overall_deadline is None or time.monotonic() < overall_deadline:
                boundary_ts = current.pair.window_end.timestamp()
                prewarm_task = asyncio.create_task(
                    _start_next_at(
                        asset=asset,
                        stats=stats,
                        boundary_ts=boundary_ts,
                        prewarm_lead_s=prewarm_lead_s,
                    ),
                    name=f"hot-prewarm-{asset}-{int(boundary_ts)}",
                )

                # Keep the outgoing window active until the exact boundary. This
                # deliberately removes the legacy +750ms post-boundary grace.
                while time.time() < boundary_ts:
                    if overall_deadline is not None and time.monotonic() >= overall_deadline:
                        prewarm_task.cancel()
                        await asyncio.gather(prewarm_task, return_exceptions=True)
                        return
                    timeout = max(0.005, min(0.1, boundary_ts - time.time()))
                    try:
                        event = await asyncio.wait_for(current.eval_queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        event = None
                    if event is not None:
                        _evaluate_event(
                            feed=current,
                            event=event,
                            kalshi_fee=kalshi_fee,
                            max_quote_skew_ms=max_quote_skew_ms,
                            capital_limit=capital_limit,
                            min_profit=min_profit,
                            active_episodes=active_episodes,
                            fh=fh,
                        )
                    if time.monotonic() >= next_heartbeat:
                        logger.info(
                            "CRYPTO_HOT_SUMMARY %s",
                            json.dumps(stats.payload(current.eval_queue), separators=(",", ":")),
                        )
                        next_heartbeat = time.monotonic() + hot.HEARTBEAT_S

                # At the quarter-hour boundary, stop evaluating the old contract and
                # atomically promote the prewarmed next feed.
                next_feed = await prewarm_task
                current.active = False
                hot._close_missing_episodes(
                    active=active_episodes,
                    qualifying={},
                    now_ms=int(boundary_ts * 1000),
                    asset=asset,
                    pair=current.pair,
                )
                await _stop_feed(current)
                next_feed.active = True
                current = next_feed
                stats.windows += 1
                fh.flush()
                logger.info(
                    "CRYPTO_HOT_DOUBLE_BUFFER_SWITCH %s",
                    json.dumps({
                        "asset": asset,
                        "switch_ms": int(time.time() * 1000),
                        "boundary_ms": int(boundary_ts * 1000),
                        "switch_after_boundary_ms": int(time.time() * 1000) - int(boundary_ts * 1000),
                        "next_window_end_utc": current.pair.window_end.isoformat(),
                        "gemini_first_seen_ms": current.gemini_first_seen_ms,
                        "kalshi_first_seen_ms": current.kalshi_first_seen_ms,
                        "both_ready_before_switch": current.both_ready_logged,
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")),
                )
    finally:
        hot._close_missing_episodes(
            active=active_episodes,
            qualifying={},
            now_ms=int(time.time() * 1000),
            asset=asset,
            pair=current.pair,
        )
        await _stop_feed(current)


# Reuse the validated fee gates and argument parser in the HOT core; replace only the
# per-asset collection loop with this double-buffered read-only implementation.
hot.collect_asset = collect_asset_prewarmed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=100)
    parser.add_argument("--capital-limit", type=Decimal, default=D("5"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/crypto-hot"))
    parser.add_argument("--prewarm-lead-s", type=float, default=1.0)
    args = parser.parse_args()

    if not (0.1 <= args.prewarm_lead_s <= 5.0):
        raise ValueError("prewarm lead must be between 0.1 and 5.0 seconds")

    # Bind requested lead to the drop-in collector without changing HOT collect_all.
    original = hot.collect_asset

    async def bound_collect_asset(**kwargs):
        return await collect_asset_prewarmed(
            **kwargs,
            prewarm_lead_s=args.prewarm_lead_s,
        )

    hot.collect_asset = bound_collect_asset
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    try:
        asyncio.run(hot.collect_all(
            assets=args.assets,
            seconds=args.seconds,
            output_dir=args.output_dir,
            max_quote_skew_ms=args.max_quote_skew_ms,
            capital_limit=args.capital_limit,
            min_profit=args.min_profit,
        ))
    finally:
        hot.collect_asset = original


if __name__ == "__main__":
    main()
