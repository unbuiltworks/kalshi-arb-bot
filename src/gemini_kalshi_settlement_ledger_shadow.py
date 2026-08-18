"""Continuous read-only Gemini/Kalshi 15-minute settlement ledger.

Polls public settled-market endpoints, matches exact 15-minute windows, and emits each
newly observed agreement row once per process. Kalshi collection is intentionally scoped
to the four exact 15-minute crypto series so settlement evidence arrives quickly instead
of paging every settled market on the exchange.

No order transport, account reads, or trading credentials are used.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_ledger_shadow")
ASSETS = ("BTC", "ETH", "SOL", "XRP")
KALSHI_15M_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}
MARGIN_BUCKETS = ("lt_1bp", "1_to_lt_2bp", "2_to_lt_5bp", "5_to_lt_10bp", "ge_10bp", "unknown")


def _margin_bucket(value: Any) -> str:
    """Bucket absolute *final Gemini settlement* margin, not entry margin."""
    if value is None:
        return "unknown"
    try:
        margin = abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return "unknown"
    if margin < Decimal("1"):
        return "lt_1bp"
    if margin < Decimal("2"):
        return "1_to_lt_2bp"
    if margin < Decimal("5"):
        return "2_to_lt_5bp"
    if margin < Decimal("10"):
        return "5_to_lt_10bp"
    return "ge_10bp"


def summarize(rows: list[Any]) -> dict[str, Any]:
    by_asset: dict[str, dict[str, int]] = defaultdict(lambda: {
        "matched": 0,
        "agreed": 0,
        "disagreed": 0,
    })
    by_final_gemini_margin: dict[str, dict[str, int]] = {
        bucket: {"matched": 0, "agreed": 0, "disagreed": 0}
        for bucket in MARGIN_BUCKETS
    }
    disagreement_abs_margins: list[Decimal] = []

    for row in rows:
        asset = str(row.asset).upper()
        if asset not in ASSETS:
            continue
        bucket = by_asset[asset]
        bucket["matched"] += 1
        bucket["agreed" if row.agrees else "disagreed"] += 1

        margin_bucket = _margin_bucket(getattr(row, "gemini_margin_bps", None))
        margin_counts = by_final_gemini_margin[margin_bucket]
        margin_counts["matched"] += 1
        margin_counts["agreed" if row.agrees else "disagreed"] += 1
        if not row.agrees:
            try:
                disagreement_abs_margins.append(abs(Decimal(str(row.gemini_margin_bps))))
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                pass

    total = sum(bucket["matched"] for bucket in by_asset.values())
    disagreements = sum(bucket["disagreed"] for bucket in by_asset.values())
    return {
        "matched_15m_windows": total,
        "agreements": total - disagreements,
        "disagreements": disagreements,
        "agreement_rate": ((total - disagreements) / total) if total else None,
        "by_asset": {asset: by_asset.get(asset, {"matched": 0, "agreed": 0, "disagreed": 0}) for asset in ASSETS},
        "by_final_gemini_abs_margin_bps": by_final_gemini_margin,
        "max_disagreement_final_gemini_abs_margin_bps": (
            str(max(disagreement_abs_margins)) if disagreement_abs_margins else None
        ),
        "margin_cohort_semantics": {
            "metric": "ABS_GEMINI_FINAL_SETTLEMENT_MINUS_GEMINI_STRIKE_BPS",
            "not_entry_margin": True,
            "buckets": {
                "lt_1bp": "[0,1)",
                "1_to_lt_2bp": "[1,2)",
                "2_to_lt_5bp": "[2,5)",
                "5_to_lt_10bp": "[5,10)",
                "ge_10bp": "[10,+inf)",
                "unknown": "missing_or_unparseable",
            },
        },
        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


async def _kalshi_settled_series_paced(
    http: httpx.AsyncClient,
    *,
    hours: int,
    series_ticker: str,
) -> list[dict[str, Any]]:
    """Fetch only one exact Kalshi 15m series with gentle pagination/backoff."""
    rows: list[dict[str, Any]] = []
    cursor = ""
    page = 0
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params: dict[str, Any] = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
            "series_ticker": series_ticker,
        }
        if cursor:
            params["cursor"] = cursor

        response: httpx.Response | None = None
        for attempt in range(6):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(8.0, 0.75 * (2 ** attempt))
            logger.warning(
                "KALSHI_SETTLED_SERIES_429 series=%s page=%s attempt=%s sleep_s=%.2f",
                series_ticker,
                page,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("markets", [])
        rows.extend(row for row in batch if isinstance(row, dict))
        cursor = str(payload.get("cursor") or "")
        page += 1
        if not cursor:
            return rows
        await asyncio.sleep(0.35)


async def collect_once(
    http: httpx.AsyncClient,
    *,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
) -> tuple[list[Any], dict[str, Any]]:
    gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
    kalshi_tasks = {
        asset: asyncio.create_task(
            _kalshi_settled_series_paced(
                http,
                hours=hours,
                series_ticker=series_ticker,
            )
        )
        for asset, series_ticker in KALSHI_15M_SERIES.items()
    }

    gemini = await gemini_task
    kalshi_by_asset = {
        asset: await task
        for asset, task in kalshi_tasks.items()
    }
    kalshi = [
        market
        for asset in ASSETS
        for market in kalshi_by_asset.get(asset, [])
    ]

    rows = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    rows = [row for row in rows if str(row.asset).upper() in ASSETS]
    diagnostics = {
        "gemini_settled_events_scanned": len(gemini),
        "kalshi_settled_markets_scanned": len(kalshi),
        "kalshi_series_counts": {
            asset: len(kalshi_by_asset.get(asset, []))
            for asset in ASSETS
        },
        "kalshi_series": KALSHI_15M_SERIES,
        "matched_true_15m_windows": len(rows),
        "gemini_sample_tickers": [str(row.get("ticker") or "") for row in gemini[:5]],
        "kalshi_sample_tickers": [str(row.get("ticker") or "") for row in kalshi[:8]],
        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    return rows, diagnostics


async def run(
    *,
    seconds: float,
    interval_s: float,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    seen: set[str] = set()
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        while deadline is None or time.monotonic() < deadline:
            started = time.monotonic()
            try:
                rows, diagnostics = await collect_once(
                    http,
                    hours=hours,
                    gemini_limit=gemini_limit,
                    max_expiry_skew_s=max_expiry_skew_s,
                )
                logger.info(
                    "CRYPTO_SETTLEMENT_LEDGER_SOURCE_COUNTS %s",
                    json.dumps(diagnostics, separators=(",", ":"), default=str),
                )
                for row in rows:
                    key = str(row.kalshi_ticker)
                    if key in seen:
                        continue
                    seen.add(key)
                    payload = row.as_dict()
                    payload.update({
                        "join_key": key,
                        "final_gemini_abs_margin_bucket": _margin_bucket(row.gemini_margin_bps),
                        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
                        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
                        "trade_authorized": False,
                        "read_only": True,
                        "order_entry": False,
                    })
                    logger.info(
                        "CRYPTO_SETTLEMENT_WINDOW %s",
                        json.dumps(payload, separators=(",", ":"), default=str),
                    )
                    if not row.agrees:
                        logger.warning(
                            "CRYPTO_SETTLEMENT_DISAGREEMENT %s",
                            json.dumps(payload, separators=(",", ":"), default=str),
                        )
                summary = summarize(rows)
                summary.update({
                    "unique_rows_emitted_this_process": len(seen),
                    "hours": hours,
                    "gemini_limit": gemini_limit,
                    "max_window_end_skew_s": max_expiry_skew_s,
                    "gemini_settled_events_scanned": diagnostics["gemini_settled_events_scanned"],
                    "kalshi_settled_markets_scanned": diagnostics["kalshi_settled_markets_scanned"],
                    "kalshi_series_counts": diagnostics["kalshi_series_counts"],
                })
                logger.info(
                    "CRYPTO_SETTLEMENT_LEDGER_SUMMARY %s",
                    json.dumps(summary, separators=(",", ":"), default=str),
                )
            except Exception:
                logger.exception(
                    "CRYPTO_SETTLEMENT_LEDGER_CYCLE_FAILED trade_authorized=false read_only=true order_entry=false"
                )

            sleep_s = max(0.0, interval_s - (time.monotonic() - started))
            if deadline is not None:
                sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
            if sleep_s:
                await asyncio.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-s", type=float, default=120.0)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=2500)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.interval_s < 60:
        raise SystemExit("--interval-s must be >=60")
    if not 1 <= args.hours <= 168:
        raise SystemExit("--hours must be 1..168")
    if not 100 <= args.gemini_limit <= 10000:
        raise SystemExit("--gemini-limit must be 100..10000")
    if not 0 <= args.max_expiry_skew_s <= 120:
        raise SystemExit("--max-expiry-skew-s must be 0..120")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTO_SETTLEMENT_LEDGER_ENABLED assets=BTC,ETH,SOL,XRP interval_s=%s hours=%s "
        "kalshi_scope=exact_15m_series window_identity=GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME "
        "margin_cohorts=final_gemini_settlement_not_entry_margin "
        "trade_authorized=false read_only=true order_entry=false",
        args.interval_s,
        args.hours,
    )
    asyncio.run(run(
        seconds=args.seconds,
        interval_s=args.interval_s,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_expiry_skew_s=args.max_expiry_skew_s,
    ))


if __name__ == "__main__":
    main()
