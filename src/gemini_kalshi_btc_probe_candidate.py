"""Read-only one-shot BTC live-probe candidate watcher.

This module is intentionally incapable of placing orders. It watches the same
production Gemini/Kalshi streams used by the fast shadow collector and exits only
when a one-contract candidate passes the exact guardrails intended for the first
real execution probe:

- current aligned BTC 15-minute contract on both venues,
- both-taker after-fee locked profit >= $0.03 for exactly one contract,
- cross-venue quote skew <= 50 ms,
- candidate remains positive for at least the configured persistence window,
- exact outbound FOK payload economics independently recompute to the same edge,
- current live Kalshi series fee multiplier is used.

The output contains the sanitized candidate and would-be order payloads for review.
No order transport is imported and no private write endpoint can be reached here.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import time
from typing import Any

import httpx

from src.gemini_kalshi_btc_ws_shadow import (
    _current_btc_pair,
    _gemini_stream,
    _kalshi_stream,
    _raise_if_stream_failed,
)
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.gemini_kalshi_live_probe_guard import (
    DEFAULT_MIN_LOCKED_PROFIT,
    MAX_QUOTE_SKEW_MS,
    build_leg_payloads,
    validate_exact_probe_payloads,
)

D = Decimal
_BACKGROUND_CLEANUPS: set[asyncio.Task] = set()


async def _drain_cancelled_stream_tasks(tasks: tuple[asyncio.Task, ...]) -> None:
    """Drain canceled stream readers away from the latency-critical return path."""
    await asyncio.gather(*tasks, return_exceptions=True)


def _cancel_stream_tasks_nowait(tasks: dict[str, asyncio.Task]) -> None:
    """Cancel stream readers immediately, but never await slow socket teardown here.

    Some websocket implementations can spend several seconds closing a canceled
    connection. A qualified candidate must reach the service send gate immediately;
    waiting for reader teardown would make every otherwise-valid quote stale.
    """
    values = tuple(tasks.values())
    for task in values:
        task.cancel()
    if not values:
        return
    cleanup = asyncio.create_task(_drain_cancelled_stream_tasks(values))
    _BACKGROUND_CLEANUPS.add(cleanup)
    cleanup.add_done_callback(_BACKGROUND_CLEANUPS.discard)


def qualified_report(
    *,
    pair,
    gemini: TopOfBook,
    kalshi: TopOfBook,
    candidate,
    kalshi_fee_multiplier: Decimal,
    persistence_ms: int,
    observed_ns: int | None = None,
) -> dict[str, Any]:
    """Build and independently validate a dry-run report for one candidate."""
    gemini_payload, kalshi_payload = build_leg_payloads(
        direction=candidate.direction,
        gemini_symbol=pair.gemini_symbol,
        kalshi_ticker=pair.kalshi_ticker,
        gemini_yes_bid=gemini.yes_bid,
        gemini_yes_ask=gemini.yes_ask,
        kalshi_yes_bid=kalshi.yes_bid,
        kalshi_yes_ask=kalshi.yes_ask,
    )
    recomputed = validate_exact_probe_payloads(
        gemini_payload,
        kalshi_payload,
        modeled_locked_profit=candidate.locked_profit,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        quote_skew_ms=candidate.quote_skew_ms,
    )
    return {
        "mode": "DRY_RUN_FIRST_PROBE_CANDIDATE",
        "qualified": True,
        "asset": "BTC",
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "window_end_utc": pair.window_end.isoformat(),
        "direction": candidate.direction,
        "quantity": "1",
        "quote_skew_ms": candidate.quote_skew_ms,
        "persistence_ms": int(persistence_ms),
        "candidate_observed_ns": int(observed_ns if observed_ns is not None else time.time_ns()),
        "modeled_locked_profit_dollars": str(candidate.locked_profit),
        "recomputed_locked_profit_dollars": str(recomputed),
        "modeled_total_cost_dollars": str(candidate.net_cost),
        "kalshi_fee_multiplier": str(kalshi_fee_multiplier),
        "gemini_payload": gemini_payload,
        "kalshi_payload": kalshi_payload,
        "read_only": True,
        "order_entry": False,
    }


async def _watch_pair(
    *,
    pair,
    kalshi_fee,
    kalshi_fee_multiplier: Decimal,
    overall_deadline: float,
    persistence_ms: int,
) -> dict[str, Any] | None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100000)
    tasks = {
        "gemini": asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue)),
        "kalshi": asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue)),
    }
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    first_positive_ms: dict[str, int] = {}
    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.25)
    pair_deadline = min(overall_deadline, time.monotonic() + remaining_window_s)

    try:
        while time.monotonic() < pair_deadline:
            _raise_if_stream_failed(tasks)
            timeout = max(0.05, min(0.5, pair_deadline - time.monotonic()))
            try:
                venue, receive_ms, _exchange_ms, top = await asyncio.wait_for(
                    queue.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                _raise_if_stream_failed(tasks)
                continue

            if venue == "gemini":
                g = top
            else:
                k = top
            if g is None or k is None:
                continue

            candidates = evaluate_pair(
                g,
                k,
                gemini_fee=gemini_predictions_taker_fee,
                kalshi_fee=kalshi_fee,
                max_quote_skew_ms=MAX_QUOTE_SKEW_MS,
                max_quantity=D("1"),
            )
            eligible = {
                c.direction: c
                for c in candidates
                if c.locked_profit >= DEFAULT_MIN_LOCKED_PROFIT
            }
            for direction in list(first_positive_ms):
                if direction not in eligible:
                    first_positive_ms.pop(direction, None)

            for direction, candidate in eligible.items():
                started = first_positive_ms.setdefault(direction, receive_ms)
                age_ms = receive_ms - started
                if age_ms < persistence_ms:
                    continue
                return qualified_report(
                    pair=pair,
                    gemini=g,
                    kalshi=k,
                    candidate=candidate,
                    kalshi_fee_multiplier=kalshi_fee_multiplier,
                    persistence_ms=age_ms,
                    observed_ns=time.time_ns(),
                )
    finally:
        # Do not put websocket close latency between candidate observation and the
        # service's final stale/economics checks. Readers are canceled immediately
        # and drained in a tracked background task.
        _cancel_stream_tasks_nowait(tasks)
    return None


async def watch(*, seconds: float, persistence_ms: int) -> dict[str, Any]:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if persistence_ms < 0:
        raise ValueError("persistence_ms cannot be negative")

    overall_deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=20.0) as http:
        fee_meta = await fetch_series_fee_metadata(http, "KXBTC15M")
        kalshi_fee = fee_meta.taker_fee_fn()
        fee_multiplier = D(fee_meta.fee_multiplier)

        while time.monotonic() < overall_deadline:
            pair = await _current_btc_pair(http)
            if pair is None:
                await asyncio.sleep(min(1.0, max(0.0, overall_deadline - time.monotonic())))
                continue
            result = await _watch_pair(
                pair=pair,
                kalshi_fee=kalshi_fee,
                kalshi_fee_multiplier=fee_multiplier,
                overall_deadline=overall_deadline,
                persistence_ms=persistence_ms,
            )
            if result is not None:
                return result

    return {
        "mode": "DRY_RUN_FIRST_PROBE_CANDIDATE",
        "qualified": False,
        "reason": "no_candidate_met_all_first_probe_gates_before_timeout",
        "minimum_locked_profit_dollars": str(DEFAULT_MIN_LOCKED_PROFIT),
        "maximum_quote_skew_ms": MAX_QUOTE_SKEW_MS,
        "minimum_persistence_ms": persistence_ms,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument("--persistence-ms", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(watch(
        seconds=args.seconds,
        persistence_ms=args.persistence_ms,
    )), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
