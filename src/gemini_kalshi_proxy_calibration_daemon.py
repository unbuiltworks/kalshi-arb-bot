"""Continuous runner for BTC proxy calibration without blocking contract rollover.

Settlement reconciliation is intentionally detached from the checkpoint hot path:
a slow venue resolution for window N must never cause us to miss T-60..T-1 data
for window N+1. Read-only; no order-entry code is imported.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import time

import httpx

from src.gemini_kalshi_proxy_calibration import (
    CHECKPOINTS_S,
    CalibrationState,
    GEMINI_PREDICTION_REST,
    KALSHI_REST,
    _checkpoint,
    _checkpoint_dict,
    _decimal,
    coinbase_stream,
    discover_pair,
    gemini_spot_poller,
    kalshi_target,
    wait_for_settlement,
)

logger = logging.getLogger("kalshi_arb.gemini_kalshi_proxy_calibration_daemon")


async def authoritative_pair(http: httpx.AsyncClient):
    """Discover, then fail-closed revalidate the exact current venue thresholds."""
    pair = await discover_pair(http)
    if pair is None:
        return None

    g_response, k_response = await asyncio.gather(
        http.get(f"{GEMINI_PREDICTION_REST}/events/{pair.gemini_event_ticker}/strike"),
        http.get(f"{KALSHI_REST}/markets/{pair.kalshi_ticker}"),
    )
    if g_response.status_code != 200 or k_response.status_code != 200:
        return None

    g_strike = _decimal(g_response.json().get("value"))
    market = k_response.json().get("market") or {}
    k_target = kalshi_target(market)
    if g_strike is None or k_target is None:
        logger.warning("PROXY_THRESHOLD_UNAVAILABLE %s", json.dumps({
            "gemini_event_ticker": pair.gemini_event_ticker,
            "kalshi_ticker": pair.kalshi_ticker,
            "gemini_strike_available": g_strike is not None,
            "kalshi_target_available": k_target is not None,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        return None

    if g_strike != pair.gemini_strike or k_target != pair.kalshi_target:
        logger.info("PROXY_THRESHOLD_REVALIDATED %s", json.dumps({
            "gemini_event_ticker": pair.gemini_event_ticker,
            "kalshi_ticker": pair.kalshi_ticker,
            "list_gemini_strike": str(pair.gemini_strike),
            "exact_gemini_strike": str(g_strike),
            "list_kalshi_target": str(pair.kalshi_target) if pair.kalshi_target is not None else None,
            "exact_kalshi_target": str(k_target),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    return replace(
        pair,
        gemini_strike=g_strike,
        kalshi_target=k_target,
        kalshi_strike_type=str(market.get("strike_type") or pair.kalshi_strike_type),
    )


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
                pair = await authoritative_pair(http)
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
                    "gemini_strike_source": "GET_EVENT_STRIKE_ENDPOINT",
                    "kalshi_ticker": pair.kalshi_ticker,
                    "kalshi_expiry": pair.kalshi_expiry.isoformat(),
                    "kalshi_target": str(pair.kalshi_target),
                    "kalshi_strike_type": pair.kalshi_strike_type,
                    "kalshi_target_source": "GET_MARKET_FLOOR_STRIKE",
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
