"""Activation-gated prewarmed read-only Gemini/Kalshi HOT scanner.

The next 15-minute contract pair is deterministic. This research scanner starts the
next pair's Gemini and Kalshi WebSocket subscriptions one second before the current
quarter-hour ends, while continuing to evaluate the outgoing pair. At the boundary it
hands off to the already-open streams.

Safety invariant: next-window events received before the boundary are discarded and
can never seed an evaluation. Only top-of-book updates timestamped at or after the
new window's activation time are eligible. The scanner remains
INDEX_MISMATCH_RESEARCH_ONLY and contains no order-entry authorization.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx

from src import gemini_kalshi_predictive_hot as predictive

hot = predictive.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_prewarmed_hot")
PREWARM_LEAD_S = 1.0


@dataclass
class StreamBundle:
    pair: Any
    queue: asyncio.Queue
    tasks: list[asyncio.Task]
    started_ms: int


def activation_ms(pair) -> int:
    """UTC millisecond timestamp at which a 15-minute pair becomes active."""
    return int((pair.window_end.timestamp() - 15 * 60) * 1000)


def event_is_activated(receive_ms: int, pair) -> bool:
    """Fail closed against using a pre-boundary prewarm event for evaluation."""
    return int(receive_ms) >= activation_ms(pair)


async def _start_bundle(asset: str, pair, stats, *, start_at_ts: float | None = None) -> StreamBundle:
    if start_at_ts is not None:
        await asyncio.sleep(max(0.0, start_at_ts - time.time()))
    queue: asyncio.Queue = asyncio.Queue(maxsize=hot.MAX_QUEUE)
    started_ms = int(time.time() * 1000)
    tasks = [
        asyncio.create_task(hot._gemini_stream(pair.gemini_symbol, queue, stats), name=f"prewarm-gemini-{asset}"),
        asyncio.create_task(hot._kalshi_stream(pair.kalshi_ticker, queue, stats), name=f"prewarm-kalshi-{asset}"),
    ]
    logger.info(
        "CRYPTO_HOT_PREWARM_STREAMS_STARTED %s",
        json.dumps({
            "asset": asset,
            "window_end_utc": pair.window_end.isoformat(),
            "activation_ms": activation_ms(pair),
            "streams_started_ms": started_ms,
            "start_relative_activation_ms": started_ms - activation_ms(pair),
            "lead_s": PREWARM_LEAD_S,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return StreamBundle(pair=pair, queue=queue, tasks=tasks, started_ms=started_ms)


async def _stop_bundle(bundle: StreamBundle) -> None:
    for task in bundle.tasks:
        task.cancel()
    await asyncio.gather(*bundle.tasks, return_exceptions=True)


async def _prewarm_next(asset: str, current_pair, stats) -> StreamBundle:
    boundary_ts = current_pair.window_end.timestamp()
    next_pair = predictive.predicted_pair(asset, now_ts=boundary_ts + 0.001)
    return await _start_bundle(
        asset,
        next_pair,
        stats,
        start_at_ts=boundary_ts - PREWARM_LEAD_S,
    )


async def _run_window(
    *,
    bundle: StreamBundle,
    asset: str,
    kalshi_fee,
    output: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    stats,
    overall_deadline: float | None,
) -> None:
    pair = bundle.pair
    queue = bundle.queue
    activation = activation_ms(pair)
    end_ms = int(pair.window_end.timestamp() * 1000)
    active: dict[str, Any] = {}
    g = None
    k = None
    latest_k_exchange_ts: int | None = None
    first_activated: dict[str, int] = {}
    activated_ready_logged = False
    next_heartbeat = time.monotonic() + hot.HEARTBEAT_S

    stats.windows += 1
    logger.info(
        "CRYPTO_HOT_PREWARM_WINDOW %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "activation_ms": activation,
            "streams_started_ms": bundle.started_ms,
            "start_relative_activation_ms": bundle.started_ms - activation,
            "max_quote_skew_ms": max_quote_skew_ms,
            "capital_limit": str(capital_limit),
            "min_profit": str(min_profit),
            "settlement_status": hot.SETTLEMENT_STATUS,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("a", encoding="utf-8") as fh:
            while time.time() * 1000 < end_ms:
                if overall_deadline is not None and time.monotonic() >= overall_deadline:
                    break
                remaining_s = max(0.0, (end_ms - time.time() * 1000) / 1000)
                if remaining_s <= 0:
                    break
                timeout = max(0.005, min(0.25, remaining_s))
                try:
                    venue, receive_ms, exchange_ms, top, trigger_mono_ns = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    continue

                # A prewarmed stream may send a snapshot before the contract becomes
                # active. Never cache or evaluate it; require a post-boundary update
                # from each venue before this window can become ready.
                if not event_is_activated(receive_ms, pair):
                    continue
                # Do not let an event dequeued just after the boundary prolong the
                # outgoing window and delay the handoff.
                if int(time.time() * 1000) >= end_ms:
                    break

                first_activated.setdefault(venue, int(receive_ms))
                if (
                    not activated_ready_logged
                    and "gemini" in first_activated
                    and "kalshi" in first_activated
                ):
                    ready_ms = max(first_activated.values())
                    activated_ready_logged = True
                    logger.info(
                        "CRYPTO_HOT_PREWARM_ACTIVATED_BOTH %s",
                        json.dumps({
                            "asset": asset,
                            "gemini_symbol": pair.gemini_symbol,
                            "kalshi_ticker": pair.kalshi_ticker,
                            "window_end_utc": pair.window_end.isoformat(),
                            "activation_ms": activation,
                            "gemini_first_activated_ms": first_activated["gemini"],
                            "kalshi_first_activated_ms": first_activated["kalshi"],
                            "both_activated_ready_ms": ready_ms,
                            "ready_after_activation_ms": ready_ms - activation,
                            "streams_started_ms": bundle.started_ms,
                            "start_relative_activation_ms": bundle.started_ms - activation,
                            "pre_boundary_events_never_evaluated": True,
                            "read_only": True,
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )

                if venue == "gemini":
                    g = top
                else:
                    k = top
                    latest_k_exchange_ts = exchange_ms
                if g is None or k is None:
                    continue

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
                    active=active,
                    qualifying=qualifying,
                    now_ms=receive_ms,
                    asset=asset,
                    pair=pair,
                )
                hot._update_episodes(
                    active=active,
                    qualifying=qualifying,
                    now_ms=receive_ms,
                    asset=asset,
                    pair=pair,
                    trigger_venue=venue,
                )

                row = {
                    "asset": asset,
                    "window_end_utc": pair.window_end.isoformat(),
                    "event_venue": venue,
                    "receive_ts_ms": receive_ms,
                    "activation_ms": activation,
                    "event_after_activation": True,
                    "gemini_receive_ts_ms": g.ts_ms,
                    "kalshi_receive_ts_ms": k.ts_ms,
                    "kalshi_exchange_ts_ms": latest_k_exchange_ts,
                    "kalshi_exchange_to_receive_ms": (
                        k.ts_ms - latest_k_exchange_ts
                        if latest_k_exchange_ts is not None
                        else None
                    ),
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "small_q_candidates": [hot._candidate_payload(q, c) for q, c in all_small],
                    "trigger_to_eval_ms": round((eval_done_ns - trigger_mono_ns) / 1_000_000, 3),
                    "settlement_status": hot.SETTLEMENT_STATUS,
                    "read_only": True,
                    "order_entry": False,
                }
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

                if time.monotonic() >= next_heartbeat:
                    logger.info(
                        "CRYPTO_HOT_PREWARM_SUMMARY %s",
                        json.dumps(stats.payload(queue), separators=(",", ":")),
                    )
                    next_heartbeat = time.monotonic() + hot.HEARTBEAT_S
            fh.flush()
    finally:
        hot._close_missing_episodes(
            active=active,
            qualifying={},
            now_ms=min(int(time.time() * 1000), end_ms),
            asset=asset,
            pair=pair,
        )


async def collect_asset(
    *,
    http: httpx.AsyncClient,
    discovery,
    asset: str,
    output_dir: Path,
    max_quote_skew_ms: int,
    capital_limit: Decimal,
    min_profit: Decimal,
    fee_meta,
    overall_deadline: float | None,
) -> None:
    """Continuously overlap next-window subscriptions with the outgoing window."""
    stats = hot.AssetStats(asset)
    kalshi_fee = fee_meta.taker_fee_fn()
    pair = await discovery.current_pair(http, asset)
    while pair is None:
        if overall_deadline is not None and time.monotonic() >= overall_deadline:
            return
        await asyncio.sleep(0.25)
        pair = await discovery.current_pair(http, asset)

    bundle = await _start_bundle(asset, pair, stats)
    try:
        while overall_deadline is None or time.monotonic() < overall_deadline:
            output = output_dir / f"crypto_hot_prewarmed_{asset.lower()}.jsonl"
            prewarm_task = asyncio.create_task(
                _prewarm_next(asset, pair, stats), name=f"prewarm-next-{asset}"
            )
            await _run_window(
                bundle=bundle,
                asset=asset,
                kalshi_fee=kalshi_fee,
                output=output,
                max_quote_skew_ms=max_quote_skew_ms,
                capital_limit=capital_limit,
                min_profit=min_profit,
                stats=stats,
                overall_deadline=overall_deadline,
            )
            await _stop_bundle(bundle)

            if overall_deadline is not None and time.monotonic() >= overall_deadline:
                prewarm_task.cancel()
                await asyncio.gather(prewarm_task, return_exceptions=True)
                return

            try:
                bundle = await prewarm_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Research failover only: preserve observation continuity without
                # pretending prewarm succeeded.
                logger.warning(
                    "CRYPTO_HOT_PREWARM_FALLBACK asset=%s type=%s error=%s",
                    asset, type(exc).__name__, str(exc)[:220],
                )
                boundary_ts = pair.window_end.timestamp()
                next_pair = predictive.predicted_pair(asset, now_ts=boundary_ts + 0.001)
                bundle = await _start_bundle(asset, next_pair, stats)
            pair = bundle.pair
    finally:
        await _stop_bundle(bundle)


def install() -> None:
    hot.collect_asset = collect_asset


def main() -> None:
    install()
    logger.info(
        "CRYPTO_HOT_PREWARMED_MODE_ENABLED lead_s=1.0 activation_gate=receive_ts_at_or_after_boundary read_only=true order_entry=false"
    )
    predictive.main()


if __name__ == "__main__":
    main()
