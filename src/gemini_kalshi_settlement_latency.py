"""Read-only settlement-latency study for Kalshi 15-minute crypto markets.

Capital tied up in a cross-venue lock is not released at the measurement-window
endpoint. Kalshi exposes ``settlement_ts`` on finalized markets, so this module
measures actual close->settlement latency for BTC/ETH/SOL/XRP 15-minute markets,
alongside expected-resolution latency and the configured settlement timer.

This is public-data research only. It has no authentication or order-entry path.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
import json
import math
from typing import Any

import httpx

from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced
from src.gemini_kalshi_window_identity import (
    kalshi_15m_asset,
    kalshi_expected_resolution,
    kalshi_window_end,
    parse_dt,
)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "median_s": _percentile(values, 0.50),
        "p90_s": _percentile(values, 0.90),
        "p95_s": _percentile(values, 0.95),
        "max_s": max(values) if values else None,
    }


def latency_rows(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market in markets:
        asset = kalshi_15m_asset(market)
        if asset is None:
            continue
        close = kalshi_window_end(market)
        settled = parse_dt(market.get("settlement_ts"))
        if close is None or settled is None:
            continue
        expected = kalshi_expected_resolution(market)
        close_to_settlement_s = (settled - close).total_seconds()
        if close_to_settlement_s < 0:
            # Fail closed on malformed or semantically incompatible timestamps.
            continue
        row = {
            "asset": asset,
            "ticker": str(market.get("ticker") or ""),
            "close_time": close.isoformat(),
            "settlement_ts": settled.isoformat(),
            "close_to_settlement_s": close_to_settlement_s,
            "settlement_timer_seconds": market.get("settlement_timer_seconds"),
        }
        if expected is not None:
            row["expected_resolution_time"] = expected.isoformat()
            row["close_to_expected_resolution_s"] = (expected - close).total_seconds()
            row["expected_resolution_to_settlement_s"] = (settled - expected).total_seconds()
        rows.append(row)
    rows.sort(key=lambda r: (r["close_time"], r["asset"]))
    return rows


def summarize(markets: list[dict[str, Any]]) -> dict[str, Any]:
    rows = latency_rows(markets)
    by_asset_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_asset_rows[row["asset"]].append(row)

    by_asset: dict[str, Any] = {}
    for asset, asset_rows in sorted(by_asset_rows.items()):
        close_to_settlement = [float(r["close_to_settlement_s"]) for r in asset_rows]
        close_to_expected = [
            float(r["close_to_expected_resolution_s"])
            for r in asset_rows
            if r.get("close_to_expected_resolution_s") is not None
        ]
        expected_to_settlement = [
            float(r["expected_resolution_to_settlement_s"])
            for r in asset_rows
            if r.get("expected_resolution_to_settlement_s") is not None
        ]
        timers = [
            float(r["settlement_timer_seconds"])
            for r in asset_rows
            if r.get("settlement_timer_seconds") is not None
        ]
        by_asset[asset] = {
            "markets": len(asset_rows),
            "close_to_settlement": _summary(close_to_settlement),
            "close_to_expected_resolution": _summary(close_to_expected),
            "expected_resolution_to_settlement": _summary(expected_to_settlement),
            "configured_settlement_timer": _summary(timers),
        }

    all_close_to_settlement = [float(r["close_to_settlement_s"]) for r in rows]
    return {
        "matched_15m_markets": len(rows),
        "close_to_settlement": _summary(all_close_to_settlement),
        "by_asset": by_asset,
        "capital_lock_implication": (
            "CROSS_VENUE_CAPITAL_HOURS_SHOULD_INCLUDE_POST_CLOSE_SETTLEMENT_LATENCY; "
            "WINDOW_END_ONLY_IS_A_LOWER_BOUND"
        ),
        "rows": rows,
        "read_only": True,
        "order_entry": False,
    }


async def run(hours: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        markets = await _kalshi_settled_paced(http, hours)
    result = summarize(markets)
    result["hours"] = hours
    result["kalshi_settled_markets_scanned"] = len(markets)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    args = parser.parse_args()
    if args.hours <= 0:
        raise SystemExit("--hours must be positive")
    print(json.dumps(asyncio.run(run(args.hours)), separators=(",", ":")))


if __name__ == "__main__":
    main()
