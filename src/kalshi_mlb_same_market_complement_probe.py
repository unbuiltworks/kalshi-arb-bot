"""Read-only fee-inclusive YES+NO complement probe for Kalshi MLB markets.

For one binary Kalshi market, owning one YES and one NO is mechanically a $1
claim under the same contract.  This probe asks whether executable q=1 taker asks
plus both current taker fees ever cost less than $1.  It imports no order
transport, uses only public market/series endpoints, and never authenticates.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.three_venue_mlb_price_scan import _kalshi_series
from src.three_venue_mlb_snapshot_pricing import (
    _kalshi_markets,
    _series_multipliers,
    kalshi_nested_top_book,
)
from src.universal_three_venue_sports import fetch_kalshi_mlb

D = Decimal


def _safe_label(market: Mapping[str, Any]) -> str:
    for key in ("title", "subtitle", "yes_sub_title", "ticker"):
        value = str(market.get(key) or "").strip()
        if value:
            return value[:180]
    return ""


async def probe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        events = await fetch_kalshi_mlb(http)
        markets = _kalshi_markets(events)
        market_to_series = _kalshi_series(events)
        series = sorted({value for value in market_to_series.values() if value})
        errors: list[dict[str, str]] = []
        multipliers = await _series_multipliers(http, asyncio.Semaphore(8), series, errors)

    rows: list[dict[str, Any]] = []
    positive: list[dict[str, Any]] = []
    priced = 0
    fee_blocked = 0
    for ticker, market in markets.items():
        book = kalshi_nested_top_book(market)
        if book is None:
            continue
        series_ticker = market_to_series.get(ticker, "")
        multiplier = multipliers.get(series_ticker)
        if multiplier is None:
            fee_blocked += 1
            continue
        if book.yes_depth < D("1") or book.no_depth < D("1"):
            continue

        priced += 1
        yes_fee = kalshi_quadratic_taker_fee(book.yes_ask, D("1"), fee_multiplier=multiplier)
        no_fee = kalshi_quadratic_taker_fee(book.no_ask, D("1"), fee_multiplier=multiplier)
        raw_cost = book.yes_ask + book.no_ask
        all_in = raw_cost + yes_fee + no_fee
        profit = D("1") - all_in
        row = {
            "ticker": ticker,
            "series": series_ticker,
            "label": _safe_label(market),
            "yes_ask": str(book.yes_ask),
            "yes_depth": str(book.yes_depth),
            "no_ask": str(book.no_ask),
            "no_depth": str(book.no_depth),
            "raw_pair_cost": str(raw_cost),
            "yes_taker_fee_q1": str(yes_fee),
            "no_taker_fee_q1": str(no_fee),
            "all_in_pair_cost": str(all_in),
            "locked_profit_q1": str(profit),
        }
        rows.append(row)
        if profit > 0:
            positive.append(row)

    rows.sort(key=lambda row: (D(row["all_in_pair_cost"]), row["ticker"]))
    positive.sort(key=lambda row: (-D(row["locked_profit_q1"]), row["ticker"]))
    best = rows[0] if rows else None
    return {
        "mode": "KALSHI_MLB_SAME_MARKET_COMPLEMENT_PROBE",
        "raw_events": len(events),
        "markets_seen": len(markets),
        "q1_markets_priced": priced,
        "fee_blocked_markets": fee_blocked,
        "fee_metadata_errors": errors[:20],
        "positive_fee_inclusive_complements": len(positive),
        "best_all_in_pair_cost": None if best is None else best["all_in_pair_cost"],
        "best_locked_profit_q1": None if best is None else best["locked_profit_q1"],
        "positive_rows": positive[:20],
        "nearest_rows": rows[:20],
        "guarantee_scope": "SAME_BINARY_CONTRACT_ONE_YES_PLUS_ONE_NO_PAYS_ONE_DOLLAR",
        "pricing_scope": "Q1_TAKER_FEES_INCLUDED_CURRENT_SERIES_MULTIPLIER",
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "KALSHI_MLB_SAME_MARKET_COMPLEMENT "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
