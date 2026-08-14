"""Read-only Polymarket US credential and market-data probe.

This module never submits, modifies, or cancels orders. It verifies credentials
and samples active sports TOTAL markets before the Kalshi↔Polymarket shadow
scanner is enabled.
"""

from __future__ import annotations

import argparse
import json
import os

from polymarket_us import PolymarketUS


def client_from_env() -> PolymarketUS:
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret_key = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret_key:
        raise SystemExit(
            "POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required"
        )
    return PolymarketUS(key_id=key_id, secret_key=secret_key, timeout=10.0)


def _compact_market(market: dict) -> dict:
    keys = (
        "id",
        "slug",
        "question",
        "description",
        "active",
        "bestBid",
        "bestAsk",
        "liquidity",
        "volume",
        "sportsMarketType",
        "startDate",
        "endDate",
    )
    return {key: market.get(key) for key in keys if key in market}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Polymarket US API credential probe"
    )
    parser.add_argument(
        "--market-slug",
        help="Optional Polymarket US market slug to fetch BBO/order book for",
    )
    parser.add_argument(
        "--no-sports-sample",
        action="store_true",
        help="Skip the active sports TOTAL market sample",
    )
    args = parser.parse_args()

    client = client_from_env()
    try:
        balances = client.account.balances()
        print(json.dumps({"authenticated": True, "balances": balances}, default=str))

        if not args.no_sports_sample:
            markets = client.markets.list(
                {
                    "limit": 50,
                    "active": True,
                    "categories": ["sports"],
                    "sportsMarketTypes": ["TOTAL"],
                }
            )
            rows = markets.get("markets", []) if isinstance(markets, dict) else []
            print(
                json.dumps(
                    {
                        "sports_total_count": len(rows),
                        "sports_totals": [_compact_market(m) for m in rows[:50]],
                    },
                    default=str,
                )
            )

        if args.market_slug:
            bbo = client.markets.bbo(args.market_slug)
            book = client.markets.book(args.market_slug)
            print(
                json.dumps(
                    {
                        "market_slug": args.market_slug,
                        "bbo": bbo,
                        "book": book,
                    },
                    default=str,
                )
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
