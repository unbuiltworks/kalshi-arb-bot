"""Read-only public metadata check for fractional live-probe eligibility."""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"


async def main_async(tickers: list[str]) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        for ticker in tickers:
            response = await client.get(f"{BASE}/markets/{ticker}")
            response.raise_for_status()
            market = response.json().get("market") or response.json()
            print(json.dumps({
                "ticker": ticker,
                "status": market.get("status"),
                "event_ticker": market.get("event_ticker"),
                "fractional_trading_enabled": market.get("fractional_trading_enabled"),
                "price_level_structure": market.get("price_level_structure"),
                "price_ranges": market.get("price_ranges"),
                "yes_bid_dollars": market.get("yes_bid_dollars"),
                "yes_ask_dollars": market.get("yes_ask_dollars"),
                "yes_bid_size_fp": market.get("yes_bid_size_fp"),
                "yes_ask_size_fp": market.get("yes_ask_size_fp"),
                "fee_type": market.get("fee_type"),
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+")
    args = parser.parse_args()
    asyncio.run(main_async([x.upper() for x in args.tickers]))


if __name__ == "__main__":
    main()
