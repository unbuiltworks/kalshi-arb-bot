"""Sequential-evaluation safety wrapper for the double-buffered HOT scanner.

The prewarm pump is allowed to drain socket messages ahead of economics evaluation so
inactive next-window queues never back up. Evaluation, however, must reconstruct its
own Gemini/Kalshi book state in exact event order; otherwise a queued older trigger
could be evaluated against a newer book observed later by the pump.

A second invariant is enforced here: even after the feed's ``active`` flag flips at
rollover, only messages whose receive timestamp is at/after the new window boundary
may enter the economics queue. This closes the tiny race where a pre-boundary message
could still be waiting in ``raw_queue`` when activation occurs.
"""
from __future__ import annotations

import asyncio
import json
import time

from src import gemini_kalshi_predictive_prewarmed as base

hot = base.hot


async def _pump_feed_activation_safe(feed) -> None:
    """Drain all socket events but gate economics by active flag *and* timestamp."""
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
            base.logger.info(
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
                    "activation_timestamp_gate": True,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )

        # Hard fail-closed gate. ``active`` alone is insufficient because a message
        # received before the boundary might still be queued when activation flips.
        if not feed.active or int(receive_ms) < int(feed.boundary_ms):
            continue
        try:
            feed.eval_queue.put_nowait(event)
        except asyncio.QueueFull:
            feed.stats.dropped_events += 1


def _evaluate_event_sequential(
    *,
    feed,
    event: tuple,
    kalshi_fee,
    max_quote_skew_ms: int,
    capital_limit,
    min_profit,
    active_episodes,
    fh,
) -> None:
    venue, receive_ms, exchange_ms, top, trigger_mono_ns = event

    # Defense in depth: the pump is timestamp-gated, but economics rejects a stale
    # event again if one is ever injected directly or a future refactor bypasses it.
    if int(receive_ms) < int(feed.boundary_ms):
        return

    # Maintain an evaluator-owned state machine independent of the warm pump's latest
    # snapshots. This restores the proven scanner's event-order semantics.
    if venue == "gemini":
        feed.eval_gemini = top
    else:
        feed.eval_kalshi = top
        feed.eval_latest_k_exchange_ts = exchange_ms

    g = getattr(feed, "eval_gemini", None)
    k = getattr(feed, "eval_kalshi", None)
    latest_k_exchange_ts = getattr(feed, "eval_latest_k_exchange_ts", None)
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

    best_by_direction = {}
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
        "kalshi_exchange_ts_ms": latest_k_exchange_ts,
        "kalshi_exchange_to_receive_ms": (
            k.ts_ms - latest_k_exchange_ts
            if latest_k_exchange_ts is not None
            else None
        ),
        "gemini_symbol": feed.pair.gemini_symbol,
        "kalshi_ticker": feed.pair.kalshi_ticker,
        "small_q_candidates": [hot._candidate_payload(q, c) for q, c in all_small],
        "trigger_to_eval_ms": round((eval_done_ns - trigger_mono_ns) / 1_000_000, 3),
        "rollover_mode": "PREDICTIVE_DOUBLE_BUFFER_SEQUENTIAL_EVAL_ACTIVATION_GATED",
        "settlement_status": hot.SETTLEMENT_STATUS,
        "read_only": True,
        "order_entry": False,
    }
    fh.write(json.dumps(row, separators=(",", ":")) + "\n")


# Patch both the warm pump and evaluator before base.main() starts any feeds.
base._pump_feed = _pump_feed_activation_safe
base._evaluate_event = _evaluate_event_sequential


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
