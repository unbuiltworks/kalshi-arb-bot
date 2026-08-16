"""Safety wrapper for the double-buffered HOT scanner.

Three invariants are enforced:
1. evaluation reconstructs its own Gemini/Kalshi state in exact event order;
2. pre-boundary messages can never enter next-window economics, even if queued at the
   instant the feed's ``active`` flag flips; and
3. rollover activation never waits for old WebSocket shutdown. Old streams are marked
   inactive and cancelled immediately, while their potentially slow cleanup is awaited
   asynchronously after the already-ready warm feed can become active.
"""
from __future__ import annotations

import asyncio
import json
import time

from src import gemini_kalshi_predictive_prewarmed as base

hot = base.hot
_ORIGINAL_STOP_FEED = base._stop_feed
_RETIRE_TASKS: set[asyncio.Task] = set()


async def _stop_feed_nonblocking(feed) -> None:
    """Cancel an old feed immediately but never block warm-feed activation on cleanup."""
    feed.active = False
    # Cancellation signals happen synchronously; only the socket libraries' cleanup
    # awaits are allowed to continue in the background task.
    for task in feed.stream_tasks:
        task.cancel()
    if feed.pump_task is not None:
        feed.pump_task.cancel()

    async def retire() -> None:
        await _ORIGINAL_STOP_FEED(feed)

    task = asyncio.create_task(
        retire(),
        name=f"hot-retire-{feed.asset}-{int(feed.pair.window_end.timestamp())}",
    )
    _RETIRE_TASKS.add(task)

    def done(t: asyncio.Task) -> None:
        _RETIRE_TASKS.discard(t)
        if t.cancelled():
            return
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            base.logger.warning(
                "CRYPTO_HOT_RETIRE_ERROR asset=%s type=%s error=%s",
                feed.asset,
                type(exc).__name__,
                str(exc)[:220],
            )

    task.add_done_callback(done)


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
                    "nonblocking_old_feed_retirement": True,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )

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
    if int(receive_ms) < int(feed.boundary_ms):
        return

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
        "rollover_mode": "PREDICTIVE_DOUBLE_BUFFER_SEQUENTIAL_EVAL_ACTIVATION_GATED_NONBLOCKING_RETIRE",
        "settlement_status": hot.SETTLEMENT_STATUS,
        "read_only": True,
        "order_entry": False,
    }
    fh.write(json.dumps(row, separators=(",", ":")) + "\n")


base._pump_feed = _pump_feed_activation_safe
base._evaluate_event = _evaluate_event_sequential
base._stop_feed = _stop_feed_nonblocking


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
