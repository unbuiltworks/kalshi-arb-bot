"""Read-only shape diagnostic for current Polymarket US MLB event payloads."""
from __future__ import annotations

import asyncio
from collections import Counter
import json

import httpx

from src.strict_mlb_prop_snapshot import POLY, _fetch_json


async def run_once() -> dict:
    async with httpx.AsyncClient(timeout=20.0) as http:
        body = await _fetch_json(
            http,
            f"{POLY}/v2/leagues/mlb/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
    events = list(body.get("events") or [])
    market_types = Counter()
    examples = []
    total_markets = 0
    for event in events:
        for market in event.get("markets") or []:
            total_markets += 1
            market_types[str(market.get("sportsMarketType") or "<missing>")] += 1
            if len(examples) < 20:
                examples.append({
                    "event_startTime": event.get("startTime"),
                    "event_slug": event.get("slug"),
                    "sportsMarketType": market.get("sportsMarketType"),
                    "slug": market.get("slug"),
                    "question": market.get("question") or market.get("title"),
                    "line": market.get("line"),
                    "gameStartTime": market.get("gameStartTime"),
                    "startTime": market.get("startTime"),
                    "keys": sorted(str(k) for k in market.keys()),
                })
    result = {
        "events": len(events),
        "nested_markets": total_markets,
        "sports_market_types": dict(market_types.most_common()),
        "examples": examples,
        "top_level_keys": sorted(str(k) for k in body.keys()),
        "read_only": True,
        "order_entry": False,
    }
    print("POLY_MLB_SHAPE " + json.dumps(result, separators=(",", ":")))
    return result


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
