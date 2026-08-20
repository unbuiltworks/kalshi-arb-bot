"""One-shot public Kalshi NFL event schema inspection. No auth, no orders."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

URL = "https://external-api.kalshi.com/trade-api/v2/events"
SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")


async def main_async() -> dict[str, Any]:
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=20.0) as http:
        for series in SERIES:
            response = await http.get(URL, params={
                "limit": 3,
                "status": "open",
                "series_ticker": series,
                "with_nested_markets": "true",
            })
            response.raise_for_status()
            body = response.json()
            rows = [x for x in body.get("events") or [] if isinstance(x, Mapping)]
            out[series] = []
            for event in rows:
                markets = [x for x in event.get("markets") or [] if isinstance(x, Mapping)]
                market = markets[0] if markets else {}
                out[series].append({
                    "event_ticker": event.get("event_ticker"),
                    "event_title": event.get("title"),
                    "event_strike_date": event.get("strike_date"),
                    "event_expected_expiration_time": event.get("expected_expiration_time"),
                    "event_close_time": event.get("close_time"),
                    "market_ticker": market.get("ticker"),
                    "market_open_time": market.get("open_time"),
                    "market_expected_expiration_time": market.get("expected_expiration_time"),
                    "market_close_time": market.get("close_time"),
                    "market_expiration_time": market.get("expiration_time"),
                    "market_latest_expiration_time": market.get("latest_expiration_time"),
                })
            await asyncio.sleep(0.15)
    return {"status": "NFL_KALSHI_SCHEMA_PROBE", "series": out, "read_only": True, "order_entry": False}


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), separators=(",", ":"), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
