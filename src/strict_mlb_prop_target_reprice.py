"""Targeted read-only live repricer for one strict MLB prop pair.

No authentication or order entry. Intended to revalidate a scanner candidate against
fresh public books immediately before a manual micro-probe decision.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json

import httpx

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src import strict_mlb_prop_snapshot_v4 as v4


async def reprice(poly_slug: str, kalshi_ticker: str, series: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as http:
        poly_body, poly_error = await v3._fetch_optional(
            http, f"{base.POLY}/v1/markets/{poly_slug}/book"
        )
        kalshi_body, kalshi_error = await v3._fetch_optional(
            http, f"{base.KALSHI}/markets/{kalshi_ticker}/orderbook", params={"depth": 1}
        )
        series_body, series_error = await v3._fetch_optional(
            http, f"{base.KALSHI}/series/{series}"
        )

    now = datetime.now(timezone.utc).isoformat()
    if poly_body is None or kalshi_body is None or series_body is None:
        return {
            "checked_at_utc": now,
            "poly_slug": poly_slug,
            "kalshi_ticker": kalshi_ticker,
            "error": ";".join(x for x in (poly_error, kalshi_error, series_error) if x) or "missing_data",
            "read_only": True,
            "order_entry": False,
        }

    p = base.poly_top_book(poly_body)
    k = base.kalshi_top_book(kalshi_body)
    meta = dict(series_body.get("series") or {})
    multiplier = base._d(meta.get("fee_multiplier"))
    fee_type = str(meta.get("fee_type") or "")
    if p is None or k is None or multiplier is None:
        return {
            "checked_at_utc": now,
            "poly_slug": poly_slug,
            "kalshi_ticker": kalshi_ticker,
            "book_comparable": False,
            "fee_type": fee_type,
            "fee_multiplier": str(multiplier) if multiplier is not None else None,
            "read_only": True,
            "order_entry": False,
        }

    probe1 = v4.best_capped_plan(p, k, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1"))
    probe5 = v4.best_capped_plan(p, k, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5"))
    return {
        "checked_at_utc": now,
        "poly_slug": poly_slug,
        "kalshi_ticker": kalshi_ticker,
        "series": series,
        "poly_yes_ask": str(p.yes_ask),
        "poly_yes_depth": str(p.yes_depth),
        "poly_no_ask": str(p.no_ask),
        "poly_no_depth": str(p.no_depth),
        "kalshi_yes_ask": str(k.yes_ask),
        "kalshi_yes_depth": str(k.yes_depth),
        "kalshi_no_ask": str(k.no_ask),
        "kalshi_no_depth": str(k.no_depth),
        "fee_type": fee_type,
        "fee_multiplier": str(multiplier),
        "probe_1usd": probe1,
        "probe_5usd": probe5,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poly-slug", required=True)
    parser.add_argument("--kalshi-ticker", required=True)
    parser.add_argument("--series", required=True)
    args = parser.parse_args()
    result = asyncio.run(reprice(args.poly_slug, args.kalshi_ticker, args.series))
    print("TARGET_MLB_PROP_REPRICE " + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
