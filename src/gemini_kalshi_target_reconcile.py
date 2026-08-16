"""One-shot read-only reconciliation for a specific 15-minute boundary.

Waits for Gemini to publish settled metadata, then fetches only the four Kalshi 15m
crypto series and reports the true-window Gemini/Kalshi settlement comparison. This
is intended to reconcile a live proxy capture after settlement metadata arrives;
no order-entry code is imported.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import time

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_meta
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

logger = logging.getLogger("kalshi_arb.gemini_kalshi_target_reconcile")
ASSETS = {"BTC", "ETH", "SOL", "XRP"}


def _parse_target(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _gemini_target_assets(events: list[dict], target: datetime) -> set[str]:
    found: set[str] = set()
    for event in events:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, end = meta
        if duration == 15 and asset in ASSETS and abs((end - target).total_seconds()) < 1:
            found.add(asset)
    return found


async def run(*, target: datetime, timeout_s: float, poll_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini: list[dict] = []
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            gemini = await _gemini_settled(http, 5000)
            found = _gemini_target_assets(gemini, target)
            logger.info(
                "TARGET_RECONCILE_GEMINI_WAIT target=%s attempt=%d assets=%s",
                target.isoformat(), attempts, sorted(found),
            )
            if found == ASSETS:
                break
            await asyncio.sleep(poll_s)
        else:
            logger.warning(
                "TARGET_RECONCILE_TIMEOUT target=%s attempts=%d read_only=true order_entry=false",
                target.isoformat(), attempts,
            )
            return

        # Only after Gemini has all four target settlements do we touch Kalshi, and
        # then only via the four explicitly scoped 15-minute crypto series.
        kalshi = await _kalshi_settled_paced(http, hours=2)
        rows = match_settled_true_windows(gemini, kalshi, 30.0)
        target_rows = [r for r in rows if abs((r.expiry - target).total_seconds()) < 1]
        for row in target_rows:
            logger.info(
                "TARGET_RECONCILE_ROW %s",
                json.dumps(row.as_dict(), separators=(",", ":"), default=str),
            )
        logger.info(
            "TARGET_RECONCILE_SUMMARY %s",
            json.dumps({
                "target_utc": target.isoformat(),
                "rows": len(target_rows),
                "agreements": sum(1 for r in target_rows if r.agrees),
                "disagreements": sum(1 for r in target_rows if not r.agrees),
                "assets": sorted(r.asset for r in target_rows),
                "gemini_poll_attempts": attempts,
                "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="2026-08-16T21:00:00Z")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--poll-s", type=float, default=10.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(target=_parse_target(args.target), timeout_s=args.timeout_s, poll_s=args.poll_s))


if __name__ == "__main__":
    main()
