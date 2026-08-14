"""Read-only Polymarket US credential and market-data probe.

This module never submits, modifies, or cancels orders. It exists to verify the
API credentials and inspect a market/order book before the Kalshi↔Polymarket
shadow scanner is enabled.
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Polymarket US API credential probe"
    )
    parser.add_argument(
        "--market-slug",
        help="Optional Polymarket US market slug to fetch BBO/order book for",
    )
    args = parser.parse_args()

    client = client_from_env()
    try:
        balances = client.account.balances()
        # Balances prove authenticated access without placing an order.
        print(json.dumps({"authenticated": True, "balances": balances}, default=str))

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
