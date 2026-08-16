"""Read-only rollover runner for the Gemini/Kalshi aligned watcher.

The original aligned watcher discovers active 15-minute pairs once at startup. That
is useful for a single window, but an overnight run can silently keep watching an
expired pair set. This wrapper deliberately restarts discovery on a bounded cadence
so each segment picks up the currently-active BTC/ETH/SOL/XRP 15-minute windows.

Safety: this module only delegates to ``gemini_kalshi_aligned_watch.watch``. It adds
no authentication, order entry, live sizing, or trading behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import time
from typing import Awaitable, Callable

from src.gemini_kalshi_aligned_watch import watch

logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_watch_rollover")

WatchFn = Callable[..., Awaitable[None]]


def segment_seconds(*, remaining_s: float, rediscover_s: float) -> float:
    """Return the next bounded watcher segment length.

    Kept pure so rollover timing can be regression-tested without network access.
    """
    if remaining_s <= 0:
        return 0.0
    if rediscover_s <= 0:
        raise ValueError("rediscover_s must be positive")
    return min(remaining_s, rediscover_s)


async def run_rollover(
    *,
    seconds: float,
    rediscover_s: float,
    poll_s: float,
    max_window_skew_s: float,
    max_quote_skew_ms: int,
    output: Path,
    watch_fn: WatchFn = watch,
) -> None:
    """Run repeated read-only watcher segments with fresh pair discovery each time."""
    if seconds <= 0 or rediscover_s <= 0 or poll_s <= 0:
        raise ValueError("seconds, rediscover_s, and poll_s must be positive")

    deadline = time.monotonic() + seconds
    segment_index = 0
    while True:
        remaining = deadline - time.monotonic()
        this_segment = segment_seconds(remaining_s=remaining, rediscover_s=rediscover_s)
        if this_segment <= 0:
            break
        segment_index += 1
        logger.info(
            "ALIGNED_WATCH_ROLLOVER_SEGMENT index=%s seconds=%.3f rediscover_s=%.3f read_only=true order_entry=false",
            segment_index,
            this_segment,
            rediscover_s,
        )
        await watch_fn(
            seconds=this_segment,
            poll_s=poll_s,
            max_window_skew_s=max_window_skew_s,
            max_quote_skew_ms=max_quote_skew_ms,
            output=output,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8 * 60 * 60)
    parser.add_argument(
        "--rediscover-s",
        type=float,
        default=60.0,
        help="Restart pair discovery at this cadence so 15m windows roll overnight.",
    )
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/gemini_kalshi_aligned_watch.jsonl"),
    )
    args = parser.parse_args()
    if args.seconds <= 0 or args.rediscover_s <= 0 or args.poll_s <= 0:
        raise SystemExit("--seconds, --rediscover-s, and --poll-s must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(
        run_rollover(
            seconds=args.seconds,
            rediscover_s=args.rediscover_s,
            poll_s=args.poll_s,
            max_window_skew_s=args.max_window_skew_s,
            max_quote_skew_ms=args.max_quote_skew_ms,
            output=args.output,
        )
    )


if __name__ == "__main__":
    main()
