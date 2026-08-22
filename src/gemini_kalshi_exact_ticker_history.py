"""Bounded public-data-only exact-ticker history for Gemini/Kalshi 15m crypto.

Gemini's generic settled-event list currently exposes only the most recent slice of
crypto events. Fifteen-minute event tickers are deterministic, so this collector uses
the documented GET /events/{eventTicker} endpoint at <=1 request/second to retrieve a
bounded historical window without credentials. It then joins exact Kalshi 15m series
and emits threshold, terminal-basis, and transition summaries.

No account reads, credentials, order transports, approvals, or execution paths.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_basis_cushion_analyze import summarize as summarize_cushion
from src.gemini_kalshi_basis_transition_analyze import summarize as summarize_transition
from src.gemini_kalshi_fast_overlap import GEMINI_REST
from src.gemini_kalshi_settlement_probe import match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_7d import ASSETS, KALSHI_15M_SERIES, _kalshi_series
from src.gemini_kalshi_threshold_dominance_study import classify, summarize as summarize_threshold

logger = logging.getLogger("kalshi_arb.gemini_kalshi_exact_ticker_history")
REQUEST_INTERVAL_S = 1.02


def _floor_quarter(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return now.replace(minute=(now.minute // 15) * 15)


def _ticker(asset: str, expiry: datetime) -> str:
    return f"{asset}15M{expiry.strftime('%y%m%d%H%M')}"


def planned_tickers(*, now: datetime, hours: int) -> list[tuple[str, datetime, str]]:
    # Leave one full quarter-hour settlement buffer so every requested event should be terminal.
    latest = _floor_quarter(now) - timedelta(minutes=15)
    windows = hours * 4
    first = latest - timedelta(minutes=15 * (windows - 1))
    return [
        (asset, expiry, _ticker(asset, expiry))
        for i in range(windows)
        for expiry in [first + timedelta(minutes=15 * i)]
        for asset in ASSETS
    ]


async def _get_exact_event(
    http: httpx.AsyncClient,
    ticker: str,
    *,
    last_request_started: float | None,
) -> tuple[dict[str, Any] | None, float]:
    if last_request_started is not None:
        wait = REQUEST_INTERVAL_S - (time.monotonic() - last_request_started)
        if wait > 0:
            await asyncio.sleep(wait)
    started = time.monotonic()
    response: httpx.Response | None = None
    for attempt in range(6):
        response = await http.get(f"{GEMINI_REST}/events/{ticker}")
        if response.status_code != 429:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(16.0, 2.0 ** attempt)
        except ValueError:
            delay = min(16.0, 2.0 ** attempt)
        logger.warning(
            "CRYPTO_EXACT_HISTORY_GEMINI_429 ticker=%s attempt=%s sleep_s=%.2f",
            ticker,
            attempt + 1,
            delay,
        )
        await asyncio.sleep(delay)
        started = time.monotonic()
    assert response is not None
    if response.status_code == 404:
        return None, started
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None, started
    return payload, started


async def _gemini_exact_history(http: httpx.AsyncClient, *, hours: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = planned_tickers(now=datetime.now(timezone.utc), hours=hours)
    rows: list[dict[str, Any]] = []
    misses: list[str] = []
    invalid: list[str] = []
    last_started: float | None = None
    for idx, (_asset, _expiry, ticker) in enumerate(plan, start=1):
        event, last_started = await _get_exact_event(http, ticker, last_request_started=last_started)
        if event is None:
            misses.append(ticker)
        elif str(event.get("ticker") or "") != ticker:
            invalid.append(ticker)
        elif str(event.get("status") or "").lower() != "settled":
            invalid.append(ticker)
        else:
            rows.append(event)
        if idx % 60 == 0 or idx == len(plan):
            logger.info(
                "CRYPTO_EXACT_HISTORY_PROGRESS %s",
                json.dumps({
                    "requested": idx,
                    "planned": len(plan),
                    "settled_found": len(rows),
                    "not_found": len(misses),
                    "invalid_or_unsettled": len(invalid),
                    "request_interval_s": REQUEST_INTERVAL_S,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
    return rows, {
        "planned_exact_tickers": len(plan),
        "settled_found": len(rows),
        "not_found": len(misses),
        "invalid_or_unsettled": len(invalid),
        "sample_misses": misses[:8],
        "sample_invalid": invalid[:8],
        "request_interval_s": REQUEST_INTERVAL_S,
    }


async def run(hours: int, max_window_end_skew_s: float) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        # Gemini is deliberately sequential/paced; Kalshi exact-series work is cheap and
        # can happen in parallel without changing Gemini's public request cadence.
        gemini_task = asyncio.create_task(_gemini_exact_history(http, hours=hours))
        kalshi_tasks = {
            asset: asyncio.create_task(_kalshi_series(http, hours=hours + 2, series_ticker=series))
            for asset, series in KALSHI_15M_SERIES.items()
        }
        gemini, gemini_diag = await gemini_task
        kalshi_by_asset = {asset: await task for asset, task in kalshi_tasks.items()}

    kalshi = [market for asset in ASSETS for market in kalshi_by_asset.get(asset, [])]
    matched = match_settled_true_windows(gemini, kalshi, max_window_end_skew_s)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    enriched: list[dict[str, Any]] = []
    for row in matched:
        event = event_by_ticker.get(str(row.gemini_ticker))
        market = market_by_ticker.get(str(row.kalshi_ticker))
        if event is not None and market is not None:
            enriched.append(classify(row, event, market))

    payload = {
        "requested_hours": hours,
        "gemini_exact_history": gemini_diag,
        "kalshi_exact_series_counts": {
            asset: len(kalshi_by_asset.get(asset, [])) for asset in ASSETS
        },
        "matched_15m_windows": len(enriched),
        "threshold_outcomes": summarize_threshold(enriched),
        "directional_basis_cushion": summarize_cushion(enriched),
        "basis_transition": summarize_transition(enriched),
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info(
        "CRYPTO_EXACT_HISTORY_SUMMARY %s",
        json.dumps(payload, separators=(",", ":"), default=str),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    if not 1 <= args.hours <= 48:
        raise SystemExit("--hours must be 1..48 for bounded exact-ticker research")
    if not 0 <= args.max_window_end_skew_s <= 120:
        raise SystemExit("--max-window-end-skew-s must be 0..120")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTO_EXACT_HISTORY_ENABLED hours=%s assets=BTC,ETH,SOL,XRP request_interval_s=%s "
        "trade_authorized=false read_only=true order_entry=false",
        args.hours,
        REQUEST_INTERVAL_S,
    )
    asyncio.run(run(args.hours, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
