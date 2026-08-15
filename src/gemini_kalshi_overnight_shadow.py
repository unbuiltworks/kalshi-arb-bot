"""Native multi-window read-only Gemini/Kalshi 15m shadow runner.

Replaces the fragile shell loop used for overnight collection. The runner:
- verifies current public Kalshi fee metadata before every window;
- fails closed unless all four 15m crypto series use mapped quadratic fees;
- rediscoveries aligned markets at each 15-minute boundary;
- appends all synchronized samples to one JSONL file; and
- produces one both-venue-fee analysis at the end when the fee multiplier stayed
  constant for the entire run.

No authentication or order-entry path exists here.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import time

import httpx

from src.gemini_kalshi_aligned_watch import watch
from src.gemini_kalshi_kalshi_fees import SERIES, fetch_series_fee_metadata
from src.gemini_kalshi_watch_analyze import analyze_rows, load_rows

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_overnight_shadow")
WINDOW_S = 15 * 60


def seconds_to_next_boundary(epoch_s: float) -> float:
    """Seconds until the next UTC 15-minute wall-clock boundary."""
    remainder = epoch_s % WINDOW_S
    return WINDOW_S if remainder == 0 else WINDOW_S - remainder


def validated_common_multiplier(rows) -> Decimal:
    """Return one verified multiplier or fail closed.

    The aggregate analyzer currently accepts one multiplier. If Kalshi changes a
    series or different assets diverge, silently blending fee regimes would corrupt
    P&L, so the overnight run stops instead.
    """
    if len(rows) != len(SERIES):
        raise ValueError(f"expected {len(SERIES)} Kalshi fee rows, got {len(rows)}")
    by_ticker = {row.series_ticker: row for row in rows}
    missing = sorted(set(SERIES) - set(by_ticker))
    if missing:
        raise ValueError(f"missing Kalshi fee metadata: {missing}")
    unsupported = [
        ticker for ticker in SERIES
        if by_ticker[ticker].formula_status != "MAPPED_QUADRATIC_TAKER"
    ]
    if unsupported:
        raise ValueError(f"unmapped Kalshi fee type for: {unsupported}")
    multipliers = {D(by_ticker[ticker].fee_multiplier) for ticker in SERIES}
    if len(multipliers) != 1:
        raise ValueError(f"mixed Kalshi fee multipliers: {sorted(map(str, multipliers))}")
    return next(iter(multipliers))


async def fetch_verified_multiplier(http: httpx.AsyncClient) -> tuple[Decimal, list]:
    rows = await asyncio.gather(*(fetch_series_fee_metadata(http, ticker) for ticker in SERIES))
    return validated_common_multiplier(rows), list(rows)


async def run(
    *,
    hours: float,
    poll_s: float,
    max_window_skew_s: float,
    max_quote_skew_ms: int,
    boundary_guard_s: float,
    rollover_delay_s: float,
    output: Path,
    summary_output: Path,
) -> None:
    if hours <= 0:
        raise ValueError("hours must be positive")
    if poll_s <= 0:
        raise ValueError("poll_s must be positive")
    if boundary_guard_s < 0 or rollover_delay_s < 0:
        raise ValueError("boundary timing values must be non-negative")

    output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    started_wall_ms = int(time.time() * 1000)
    deadline = time.monotonic() + hours * 3600
    seen_multipliers: set[Decimal] = set()
    windows_attempted = 0

    logger.info("OVERNIGHT_SHADOW_START %s", json.dumps({
        "hours": hours,
        "poll_s": poll_s,
        "output": str(output),
        "summary_output": str(summary_output),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=20.0) as fee_http:
        while time.monotonic() < deadline:
            remaining_total = deadline - time.monotonic()
            if remaining_total <= 0:
                break

            multiplier, fee_rows = await fetch_verified_multiplier(fee_http)
            seen_multipliers.add(multiplier)
            if len(seen_multipliers) != 1:
                raise RuntimeError(
                    "Kalshi fee multiplier changed during run; refusing mixed-regime analysis"
                )

            to_boundary = seconds_to_next_boundary(time.time())
            usable = to_boundary - boundary_guard_s
            if usable <= 1.0:
                await asyncio.sleep(min(remaining_total, to_boundary + rollover_delay_s))
                continue

            cycle_seconds = min(remaining_total, usable)
            windows_attempted += 1
            logger.info("OVERNIGHT_SHADOW_WINDOW %s", json.dumps({
                "window_number": windows_attempted,
                "seconds": cycle_seconds,
                "seconds_to_boundary": to_boundary,
                "kalshi_fee_multiplier": str(multiplier),
                "fee_metadata": [row.as_dict() for row in fee_rows],
                "read_only": True,
            }, separators=(",", ":")))

            before = time.monotonic()
            await watch(
                seconds=cycle_seconds,
                poll_s=poll_s,
                max_window_skew_s=max_window_skew_s,
                max_quote_skew_ms=max_quote_skew_ms,
                output=output,
            )
            elapsed = time.monotonic() - before

            # If discovery returned immediately, avoid a tight retry loop. Otherwise
            # cross the settlement boundary before rediscovering the next contract.
            if elapsed < min(2.0, cycle_seconds):
                await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            else:
                wait = seconds_to_next_boundary(time.time())
                if wait <= boundary_guard_s + 2.0:
                    await asyncio.sleep(min(
                        wait + rollover_delay_s,
                        max(0.0, deadline - time.monotonic()),
                    ))

    rows = load_rows([output]) if output.exists() else []
    multiplier = next(iter(seen_multipliers)) if len(seen_multipliers) == 1 else None
    if multiplier is None:
        result = {
            "analysis_status": "FAILED_CLOSED_NO_SINGLE_VERIFIED_FEE_MULTIPLIER",
            "rows": len(rows),
            "windows_attempted": windows_attempted,
            "read_only": True,
            "order_entry": False,
        }
    else:
        result = analyze_rows(
            [row for row in rows if int(row.get("ts_ms", 0)) >= started_wall_ms],
            fee_multiplier=multiplier,
            max_quote_skew_ms=max_quote_skew_ms,
        )
        result["analysis_status"] = "BOTH_VENUE_TAKER_FEES_APPLIED"
        result["windows_attempted"] = windows_attempted
        result["run_started_ms"] = started_wall_ms

    summary_output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("OVERNIGHT_SHADOW_RESULT %s", json.dumps(result, separators=(",", ":")))


def _default_paths() -> tuple[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data = Path("logs") / f"gemini_kalshi_overnight_{stamp}.jsonl"
    return data, data.with_suffix(".summary.json")


def main() -> None:
    default_data, default_summary = _default_paths()
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--poll-s", type=float, default=1.0)
    parser.add_argument("--max-window-skew-s", type=float, default=30.0)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    parser.add_argument("--boundary-guard-s", type=float, default=2.0)
    parser.add_argument("--rollover-delay-s", type=float, default=3.0)
    parser.add_argument("--output", type=Path, default=default_data)
    parser.add_argument("--summary-output", type=Path, default=default_summary)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(
        hours=args.hours,
        poll_s=args.poll_s,
        max_window_skew_s=args.max_window_skew_s,
        max_quote_skew_ms=args.max_quote_skew_ms,
        boundary_guard_s=args.boundary_guard_s,
        rollover_delay_s=args.rollover_delay_s,
        output=args.output,
        summary_output=args.summary_output,
    ))


if __name__ == "__main__":
    main()
