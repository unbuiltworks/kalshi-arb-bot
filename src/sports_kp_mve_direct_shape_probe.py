"""Read-only direct inspection of one recently manufactured Kalshi sports MVE.

This module is intentionally isolated from every order-entry path.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_TICKER = "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-054D7EDE6EB"


async def run_once() -> dict[str, Any]:
    ticker = os.getenv("MVE_SHAPE_TICKER", DEFAULT_TICKER).strip()
    async with httpx.AsyncClient(timeout=20.0) as http:
        r = await http.get(f"{BASE}/markets/{ticker}")
    body = r.json() if r.content else {}
    market = body.get("market") if isinstance(body, dict) and isinstance(body.get("market"), dict) else body
    market = market if isinstance(market, dict) else {}
    keys = sorted(market.keys())
    out = {
        "status": "SPORTS_MVE_DIRECT_SHAPE_V1",
        "http_status": r.status_code,
        "ticker": ticker,
        "market_status": market.get("status"),
        "market_type": market.get("market_type"),
        "event_ticker": market.get("event_ticker"),
        "open_time": market.get("open_time"),
        "expected_expiration_time": market.get("expected_expiration_time"),
        "expiration_time": market.get("expiration_time"),
        "close_time": market.get("close_time"),
        "latest_expiration_time": market.get("latest_expiration_time"),
        "mve_selected_legs": market.get("mve_selected_legs"),
        "selected_markets": market.get("selected_markets"),
        "yes_bid_dollars": market.get("yes_bid_dollars"),
        "yes_bid_size_fp": market.get("yes_bid_size_fp"),
        "yes_ask_dollars": market.get("yes_ask_dollars"),
        "yes_ask_size_fp": market.get("yes_ask_size_fp"),
        "no_bid_dollars": market.get("no_bid_dollars"),
        "no_ask_dollars": market.get("no_ask_dollars"),
        "keys": keys,
        "read_only": True,
        "market_creation": False,
        "rfq": False,
        "order_entry": False,
    }
    print("SPORTS_MVE_DIRECT_SHAPE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
