"""Read-only rollover supervisor for the Gemini/Kalshi aligned crypto watcher.

The underlying aligned watcher discovers expiry-matched 15-minute contracts when a
watch segment starts. A long-running service therefore needs periodic rediscovery so
it does not keep watching an expired contract set after a window rolls. This module
runs short contiguous watch segments against the same append-only JSONL file.

Safety: this imports only the read-only watcher. It has no credentials, order-entry,
or remote-arm path and never changes live risk or sizing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import time
from typing import Awaitable, Callable

from src.gemini_kalshi_aligned_watch import watch

logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_supervisor")

WatchFn = Callable[..., Awaitable[None]]


async def supervise(
    *,
    total_seconds: float,
    segment_seconds: float,
    poll_s: float,
    max_window_skew_s: float,
    max_quote_skew_ms: int,
    output: Path,
    watch_fn: WatchFn = watch,
) -> dict[str, float | int | bool]:
    """Run repeated read-only watch segments, rediscovering pairs each segment."""
    if total_seconds <= 0:
        raise ValueError("total_seconds must be positive")
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be positive")

    started = time.monotonic()
    deadline = started + total_seconds
    segments = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        segment = min(segment_seconds, remaining)
        logger.info(
            "ALIGNED_WATCH_ROLLOVER_SEGMENT_START segment=%s seconds=%.3f read_only=true order_entry=false",
            segments + 1,
            segment,
        )
        await watch_fn(
            seconds=segment,
            poll_s=poll_s,
            max_window_skew_s=max_window_skew_s,
            max_quote_skew_ms=max_quote_skew_ms,
            output=output,
        )
        segments += 1

    return {
        "segments": segments,
        "elapsed_seconds": max(0.0, time.monotonic() - started),
        "read_only": True,
        "order_entry": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3600.0, help="total supervisor runtime")
    parser.add_argument(
        "--segment-seconds",
        type=float,
        default=60.0,
        help="rediscover expiry-matched contracts at least this often",
    )
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--max-window-skew-s", type=float, default=2.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument("--output", type=Path, default=Path("data/gemini_kalshi_aligned_watch.jsonl"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    summary = asyncio.run(
        supervise(
            total_seconds=args.seconds,
            segment_seconds=args.segment_seconds,
            poll_s=args.poll_s,
            max_window_skew_s=args.max_window_skew_s,
            max_quote_skew_ms=args.max_quote_skew_ms,
            output=args.output,
        )
    )
    logger.info("ALIGNED_WATCH_ROLLOVER_SUMMARY %s", summary)


if __name__ == "__main__":
    main()
