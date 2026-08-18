"""Continuous read-only Gemini/Kalshi 15-minute settlement ledger.

Polls public settled-market endpoints, matches exact 15-minute windows, and emits each
newly observed agreement row once per process. This creates durable Railway log evidence
that later opportunity-matrix candidates can be joined against by Kalshi ticker.

No order transport, account reads, or trading credentials are used.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_ledger_shadow")
ASSETS = ("BTC", "ETH", "SOL", "XRP")


def summarize(rows: list[Any]) -> dict[str, Any]:
    by_asset: dict[str, dict[str, int]] = defaultdict(lambda: {
        "matched": 0,
        "agreed": 0,
        "disagreed": 0,
    })
    for row in rows:
        asset = str(row.asset).upper()
        if asset not in ASSETS:
            continue
        bucket = by_asset[asset]
        bucket["matched"] += 1
        bucket["agreed" if row.agrees else "disagreed"] += 1
    total = sum(bucket["matched"] for bucket in by_asset.values())
    disagreements = sum(bucket["disagreed"] for bucket in by_asset.values())
    return {
        "matched_15m_windows": total,
        "agreements": total - disagreements,
        "disagreements": disagreements,
        "agreement_rate": ((total - disagreements) / total) if total else None,
        "by_asset": {asset: by_asset.get(asset, {"matched": 0, "agreed": 0, "disagreed": 0}) for asset in ASSETS},
        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


async def collect_once(
    http: httpx.AsyncClient,
    *,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
) -> tuple[list[Any], dict[str, Any]]:
    gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
    kalshi_task = asyncio.create_task(_kalshi_settled_paced(http, hours))
    gemini, kalshi = await asyncio.gather(gemini_task, kalshi_task)
    rows = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    rows = [row for row in rows if str(row.asset).upper() in ASSETS]
    diagnostics = {
        "gemini_settled_events_scanned": len(gemini),
        "kalshi_settled_markets_scanned": len(kalshi),
        "matched_true_15m_windows": len(rows),
        "gemini_sample_tickers": [str(row.get("ticker") or "") for row in gemini[:5]],
        "kalshi_sample_tickers": [str(row.get("ticker") or "") for row in kalshi[:5]],
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
        "window_identity=GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME "
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
