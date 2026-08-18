"""Continuous runner for BTC proxy calibration without blocking contract rollover.

Settlement reconciliation is intentionally detached from the checkpoint hot path:
a slow venue resolution for window N must never cause us to miss T-60..T-1 data
for window N+1.  Read-only; no order-entry code is imported.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import time

import httpx

from src.gemini_kalshi_proxy_calibration import (
    CHECKPOINTS_S,
    CalibrationState,
    _checkpoint,
    _checkpoint_dict,
    coinbase_stream,
    discover_pair,
    gemini_spot_poller,
    wait_for_settlement,
)

logger = logging.getLogger("kalshi_arb.gemini_kalshi_proxy_calibration_daemon")


async def run(*, seconds: float, spot_poll_s: float) -> None:
    state = CalibrationState()
    settlement_tasks: set[asyncio.Task[None]] = set()

    async with httpx.AsyncClient(timeout=10.0) as http:
        feeds = [
            asyncio.create_task(coinbase_stream(state), name="coinbase_public_ticker"),
            asyncio.create_task(gemini_spot_poller(http, state, spot_poll_s), name="gemini_public_spot"),
        ]
        stop_at = time.monotonic() + seconds if seconds > 0 else None
        last_pair_key: tuple[str, str] | None = None
        try:
            while stop_at is None or time.monotonic() < stop_at:
                pair = await discover_pair(http)
                if pair is None:
                    await asyncio.sleep(2)
                    continue

                pair_key = (pair.gemini_event_ticker, pair.kalshi_ticker)
                if pair_key == last_pair_key:
                    await asyncio.sleep(0.5)
                    continue
                last_pair_key = pair_key

                logger.info("PROXY_PAIR %s", json.dumps({
                    "gemini_event_ticker": pair.gemini_event_ticker,
                    "gemini_symbol": pair.gemini_symbol,
                    "gemini_expiry": pair.gemini_expiry.isoformat(),
                    "gemini_strike": str(pair.gemini_strike),
                    "gemini_source_agency": pair.gemini_source_agency,
                    "gemini_source_index": pair.gemini_source_index,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "kalshi_expiry": pair.kalshi_expiry.isoformat(),
                    "kalshi_target": str(pair.kalshi_target) if pair.kalshi_target is not None else None,
                    "kalshi_strike_type": pair.kalshi_strike_type,
                    "settlement_join_mode": "BACKGROUND_NONBLOCKING",
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))

                emitted: set[int] = set()
                checkpoints = []
                while datetime.now(timezone.utc) < pair.gemini_expiry:
                    now_ms = int(time.time() * 1000)
                    seconds_left = pair.gemini_expiry.timestamp() - now_ms / 1000
                    for checkpoint_s in CHECKPOINTS_S:
                        if checkpoint_s not in emitted and seconds_left <= checkpoint_s:
                            c = _checkpoint(pair, state, checkpoint_s, now_ms)
                            checkpoints.append(c)
                            emitted.add(checkpoint_s)
                            logger.info("PROXY_CHECKPOINT %s", json.dumps(
                                _checkpoint_dict(pair, c), separators=(",", ":")
                            ))
                    await asyncio.sleep(0.05 if seconds_left <= 10 else 0.25)

                task = asyncio.create_task(
                    wait_for_settlement(http, pair, checkpoints),
                    name=f"settlement:{pair.gemini_event_ticker}:{pair.kalshi_ticker}",
                )
                settlement_tasks.add(task)
                task.add_done_callback(settlement_tasks.discard)
                # Do not await it. Immediately discover the next 15-minute pair.
                await asyncio.sleep(0.05)
        finally:
            for task in feeds:
                task.cancel()
            for task in settlement_tasks:
                task.cancel()
            await asyncio.gather(*feeds, *settlement_tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0, help="0 runs continuously")
    parser.add_argument("--spot-poll-s", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(seconds=args.seconds, spot_poll_s=args.spot_poll_s))


if __name__ == "__main__":
    main()
