"""One-shot public Kalshi MLB home-run inventory probe.

Fetches open KXMLBHR events with nested markets and prints only non-secret identity
metadata needed to build an exact FanDuel counterpart adapter. No auth or order entry.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXMLBHR"


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            f"{KALSHI}/events",
            params={
                "limit": 200,
                "status": "open",
                "series_ticker": SERIES,
                "with_nested_markets": "true",
            },
        )
        response.raise_for_status()
        body = response.json()

    events = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
    samples: list[dict[str, Any]] = []
    market_count = 0
    events_with_markets = 0
    for event in events:
        markets = [row for row in event.get("markets") or [] if isinstance(row, Mapping)]
        market_count += len(markets)
        events_with_markets += int(bool(markets))
        for market in markets[:8]:
            if len(samples) >= 60:
                break
            samples.append({
                "event_ticker": event.get("event_ticker"),
                "series_ticker": event.get("series_ticker"),
                "event_title": event.get("title"),
                "market_ticker": market.get("ticker"),
                "market_title": market.get("title"),
                "yes_sub_title": market.get("yes_sub_title"),
                "no_sub_title": market.get("no_sub_title"),
                "open_time": market.get("open_time"),
                "close_time": market.get("close_time"),
                "expected_expiration_time": market.get("expected_expiration_time"),
            })
        if len(samples) >= 60:
            break
    return {
        "status": "KALSHI_MLB_HR_INVENTORY",
        "series": SERIES,
        "open_events": len(events),
        "events_with_markets": events_with_markets,
        "open_markets": market_count,
        "samples": samples,
        "read_only": True,
        "order_entry": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
