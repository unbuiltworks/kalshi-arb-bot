"""Sequential-evaluation safety wrapper for the double-buffered HOT scanner.

The prewarm pump is allowed to drain socket messages ahead of economics evaluation so
inactive next-window queues never back up. Evaluation, however, must reconstruct its
own Gemini/Kalshi book state in exact event order; otherwise a queued older trigger
could be evaluated against a newer book observed later by the pump.
"""
from __future__ import annotations

import json
import time

from src import gemini_kalshi_predictive_prewarmed as base

hot = base.hot


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
        "rollover_mode": "PREDICTIVE_DOUBLE_BUFFER_SEQUENTIAL_EVAL",
        "settlement_status": hot.SETTLEMENT_STATUS,
        "read_only": True,
        "order_entry": False,
    }
    fh.write(json.dumps(row, separators=(",", ":")) + "\n")


base._evaluate_event = _evaluate_event_sequential


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
