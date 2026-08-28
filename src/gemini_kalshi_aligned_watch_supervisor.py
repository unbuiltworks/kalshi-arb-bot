"""Continuous read-only supervisor for the 15-minute Gemini/Kalshi shadow watcher.

The underlying watcher intentionally discovers exact expiry-matched pairs at startup.
That is correct for a bounded capture, but a long-running service must rediscover pairs
across 15-minute contract rollover.  This supervisor runs short bounded capture cycles,
so every cycle reloads current Gemini/Kalshi identities and current Kalshi Series fee
metadata before collecting more shadow evidence.

Safety boundary:
- read-only public market data only
- no credentials or order-entry code
- settlement remains INDEX_MISMATCH_RESEARCH_ONLY
- a cycle failure is logged and retried; it never arms trading
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from src.gemini_kalshi_aligned_watch import watch

logger = logging.getLogger("kalshi_arb.gemini_kalshi_aligned_watch_supervisor")


async def supervise(
    *,
    cycle_seconds: float,
    poll_s: float,
    max_window_skew_s: float,
    max_quote_skew_ms: int,
    output: Path,
    retry_delay_s: float = 2.0,
    max_cycles: int | None = None,
) -> None:
    """Run bounded watcher cycles forever (or ``max_cycles`` in tests).

    Keeping each cycle shorter than a 15-minute market guarantees periodic identity
    rediscovery instead of carrying an expired pair indefinitely.  A cycle that finds
    no current pair simply returns; the supervisor retries after ``retry_delay_s``.
    """
    if cycle_seconds <= 0 or poll_s <= 0 or retry_delay_s < 0:
        raise ValueError("cycle_seconds/poll_s must be positive and retry_delay_s non-negative")
    if cycle_seconds >= 900:
        raise ValueError("cycle_seconds must be < 900 to force intra-window rediscovery")

    completed = 0
    while max_cycles is None or completed < max_cycles:
        try:
            await watch(
                seconds=cycle_seconds,
                poll_s=poll_s,
                max_window_skew_s=max_window_skew_s,
                max_quote_skew_ms=max_quote_skew_ms,
                output=output,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ALIGNED_WATCH_SUPERVISOR_CYCLE_ERROR")
        finally:
            completed += 1

        if max_cycles is not None and completed >= max_cycles:
            break
        if retry_delay_s:
            await asyncio.sleep(retry_delay_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle-seconds", type=float, default=120.0)
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument("--retry-delay-s", type=float, default=2.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/gemini_kalshi_aligned_watch.jsonl"),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(
        supervise(
            cycle_seconds=args.cycle_seconds,
            poll_s=args.poll_s,
            max_window_skew_s=args.max_window_skew_s,
            max_quote_skew_ms=args.max_quote_skew_ms,
            output=args.output,
            retry_delay_s=args.retry_delay_s,
        )
    )


if __name__ == "__main__":
    main()
