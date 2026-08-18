"""Continuous read-only BTC proxy calibration without blocking contract rollover.

A slow venue resolution for window N must never cause us to miss T-60..T-1 data
for window N+1. The daemon also samples the two-source public spot composite so
we can compare instantaneous, rolling-10s and rolling-60s proxies to the eventual
Kaiko/Gemini settlement. No order-entry code is imported.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_proxy_calibration import (
    CHECKPOINTS_S,
    CalibrationState,
    GEMINI_PREDICTION_REST,
    KALSHI_REST,
    Pair,
    _checkpoint,
    _checkpoint_dict,
    _decimal,
    _event_settlement,
    _kalshi_settlement,
    coinbase_stream,
    composite_snapshot,
    discover_pair,
    gemini_spot_poller,
    kalshi_target,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_proxy_calibration_daemon")


async def authoritative_pair(http: httpx.AsyncClient) -> Pair | None:
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


async def proxy_sampler(
    state: CalibrationState,
    samples: deque[tuple[int, Decimal]],
    *,
    sample_s: float = 0.25,
) -> None:
    """Sample only fresh two-source composites and keep ~70 seconds in memory."""
    while True:
        now_ms = int(time.time() * 1000)
        composite, count, _, _ = composite_snapshot(state.quotes, now_ms=now_ms)
        if composite is not None and count >= 2:
            samples.append((now_ms, composite))
        cutoff = now_ms - 70_000
        while samples and samples[0][0] < cutoff:
            samples.popleft()
        await asyncio.sleep(sample_s)


def rolling_mean(
    samples: deque[tuple[int, Decimal]], *, now_ms: int, horizon_ms: int
) -> tuple[Decimal | None, int]:
    values = [price for ts, price in samples if now_ms - horizon_ms <= ts <= now_ms]
    if not values:
        return None, 0
    return sum(values, D("0")) / D(len(values)), len(values)


def _prediction(price: Decimal | None, target: Decimal) -> str | None:
    if price is None:
        return None
    return "yes" if price > target else "no"


async def wait_for_settlement_enriched(
    http: httpx.AsyncClient,
    pair: Pair,
    checkpoints: list[dict[str, Any]],
    *,
    timeout_s: float = 900,
) -> None:
    """Join one completed checkpoint set to both venue outcomes in the background."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        g_value, k = await asyncio.gather(
            _event_settlement(http, pair.gemini_event_ticker),
            _kalshi_settlement(http, pair.kalshi_ticker),
        )
        k_result, k_value = k
        if g_value is not None and k_result in {"yes", "no"}:
            g_outcome = "yes" if g_value > pair.gemini_strike else "no"
            settled_checkpoints: list[dict[str, Any]] = []
            for cp in checkpoints:
                row = dict(cp)
                for key in ("composite_price", "composite_10s_mean", "composite_60s_mean"):
                    price = _decimal(row.get(key))
                    error = price - g_value if price is not None else None
                    pred = _prediction(price, pair.gemini_strike)
                    prefix = {
                        "composite_price": "instant",
                        "composite_10s_mean": "rolling_10s",
                        "composite_60s_mean": "rolling_60s",
                    }[key]
                    row[f"{prefix}_error_vs_gemini_settlement_dollars"] = str(error) if error is not None else None
                    row[f"{prefix}_abs_error_dollars"] = str(abs(error)) if error is not None else None
                    row[f"{prefix}_gemini_outcome"] = pred
                    row[f"{prefix}_gemini_outcome_correct"] = pred == g_outcome if pred else None
                settled_checkpoints.append(row)

            payload = {
                "asset": "BTC",
                "window_end_utc": pair.gemini_expiry.isoformat(),
                "gemini_event_ticker": pair.gemini_event_ticker,
                "gemini_symbol": pair.gemini_symbol,
                "gemini_source_index": pair.gemini_source_index,
                "gemini_strike": str(pair.gemini_strike),
                "gemini_settlement_value": str(g_value),
                "gemini_outcome": g_outcome,
                "kalshi_ticker": pair.kalshi_ticker,
                "kalshi_target": str(pair.kalshi_target) if pair.kalshi_target is not None else None,
                "kalshi_result": k_result,
                "kalshi_expiration_value": str(k_value) if k_value is not None else None,
                "outcomes_agree": g_outcome == k_result,
                "checkpoints": settled_checkpoints,
                "proxy_methods": ["instant", "rolling_10s", "rolling_60s"],
                "classification": "INDEX_MISMATCH_CALIBRATION_ONLY",
                "read_only": True,
                "order_entry": False,
            }
            logger.info("PROXY_SETTLEMENT %s", json.dumps(payload, separators=(",", ":")))
            return
        await asyncio.sleep(5)

    logger.warning("PROXY_SETTLEMENT_TIMEOUT %s", json.dumps({
        "gemini_event_ticker": pair.gemini_event_ticker,
        "kalshi_ticker": pair.kalshi_ticker,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


async def run(*, seconds: float, spot_poll_s: float) -> None:
    state = CalibrationState()
    samples: deque[tuple[int, Decimal]] = deque(maxlen=1000)
    settlement_tasks: set[asyncio.Task[None]] = set()

    async with httpx.AsyncClient(timeout=10.0) as http:
        feeds = [
            asyncio.create_task(coinbase_stream(state), name="coinbase_public_ticker"),
            asyncio.create_task(gemini_spot_poller(http, state, spot_poll_s), name="gemini_public_spot"),
            asyncio.create_task(proxy_sampler(state, samples), name="two_source_proxy_sampler"),
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
                    "proxy_methods": ["instant", "rolling_10s", "rolling_60s"],
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))

                emitted: set[int] = set()
                checkpoints: list[dict[str, Any]] = []
                while datetime.now(timezone.utc) < pair.gemini_expiry:
                    now_ms = int(time.time() * 1000)
                    seconds_left = pair.gemini_expiry.timestamp() - now_ms / 1000
                    for checkpoint_s in CHECKPOINTS_S:
                        if checkpoint_s not in emitted and seconds_left <= checkpoint_s:
                            c = _checkpoint(pair, state, checkpoint_s, now_ms)
                            payload = _checkpoint_dict(pair, c)
                            mean10, n10 = rolling_mean(samples, now_ms=now_ms, horizon_ms=10_000)
                            mean60, n60 = rolling_mean(samples, now_ms=now_ms, horizon_ms=60_000)
                            payload.update({
                                "composite_10s_mean": str(mean10) if mean10 is not None else None,
                                "composite_10s_samples": n10,
                                "composite_60s_mean": str(mean60) if mean60 is not None else None,
                                "composite_60s_samples": n60,
                            })
                            checkpoints.append(payload)
                            emitted.add(checkpoint_s)
                            logger.info("PROXY_CHECKPOINT %s", json.dumps(payload, separators=(",", ":")))
                    await asyncio.sleep(0.05 if seconds_left <= 10 else 0.25)

                task = asyncio.create_task(
                    wait_for_settlement_enriched(http, pair, checkpoints),
                    name=f"settlement:{pair.gemini_event_ticker}:{pair.kalshi_ticker}",
                )
                settlement_tasks.add(task)
                task.add_done_callback(settlement_tasks.discard)
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
