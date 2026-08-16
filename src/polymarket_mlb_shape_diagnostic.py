"""Read-only shape diagnostic for current Polymarket US MLB market surfaces."""
from __future__ import annotations

import asyncio
from collections import Counter
import json

import httpx

from src.strict_mlb_prop_adapters import FAMILY_SERIES
from src.strict_mlb_prop_snapshot import POLY, _fetch_json


async def _fetch_all_props(http: httpx.AsyncClient) -> list[dict]:
    """Read the documented direct market surface, paging defensively."""
    rows: list[dict] = []
    limit = 50
    for offset in range(0, 5000, limit):
        body = await _fetch_json(
            http,
            f"{POLY}/v1/markets",
            params={
                "limit": limit,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "categories": "sports",
                "sportsMarketTypes": "SPORTS_MARKET_TYPE_PROP",
            },
        )
        page = list(body.get("markets") or [])
        rows.extend(page)
        if len(page) < limit:
            break
    return rows


def _sample(market: dict) -> dict:
    return {
        "sportsMarketType": market.get("sportsMarketType"),
        "sportsMarketTypeV2": market.get("sportsMarketTypeV2"),
        "slug": market.get("slug"),
        "question": market.get("question") or market.get("title"),
        "line": market.get("line"),
        "gameId": market.get("gameId"),
        "gameStartTime": market.get("gameStartTime"),
        "startTime": market.get("startTime"),
        "keys": sorted(str(k) for k in market.keys()),
    }


async def run_once() -> dict:
    async with httpx.AsyncClient(timeout=20.0) as http:
        league_body, direct_props = await asyncio.gather(
            _fetch_json(
                http,
                f"{POLY}/v2/leagues/mlb/events",
                params={"limit": 1000, "active": "true", "closed": "false"},
            ),
            _fetch_all_props(http),
        )

    events = list(league_body.get("events") or [])
    nested_types = Counter()
    nested_examples = []
    nested_total = 0
    for event in events:
        for market in event.get("markets") or []:
            nested_total += 1
            nested_types[str(market.get("sportsMarketType") or "<missing>")] += 1
            if len(nested_examples) < 12:
                row = _sample(market)
                row["event_startTime"] = event.get("startTime")
                row["event_slug"] = event.get("slug")
                nested_examples.append(row)

    direct_type_counts = Counter(str(m.get("sportsMarketType") or "<missing>") for m in direct_props)
    mlb_direct = [
        m for m in direct_props
        if str(m.get("slug") or "").lower().startswith("astatc-mlb-")
        or " mlb " in (" " + str(m.get("question") or m.get("title") or "").lower() + " ")
    ]
    supported_direct = [m for m in mlb_direct if str(m.get("sportsMarketType") or "") in FAMILY_SERIES]

    result = {
        "league_events": len(events),
        "league_nested_markets": nested_total,
        "league_sports_market_types": dict(nested_types.most_common()),
        "direct_open_sports_props": len(direct_props),
        "direct_prop_legacy_types": dict(direct_type_counts.most_common()),
        "direct_mlb_props": len(mlb_direct),
        "direct_supported_mlb_props": len(supported_direct),
        "direct_supported_by_family": dict(Counter(str(m.get("sportsMarketType")) for m in supported_direct)),
        "direct_mlb_examples": [_sample(m) for m in mlb_direct[:20]],
        "league_examples": nested_examples,
        "diagnosis": (
            "DIRECT_MLB_PROPS_AVAILABLE_NOT_NESTED"
            if supported_direct and not any(t in FAMILY_SERIES for t in nested_types)
            else "DIRECT_AND_NESTED_SUPPORTED_PROPS_AVAILABLE"
            if supported_direct
            else "NO_SUPPORTED_MLB_PROPS_ON_DIRECT_MARKET_SURFACE"
        ),
        "read_only": True,
        "order_entry": False,
    }
    print("POLY_MLB_SHAPE " + json.dumps(result, separators=(",", ":")))
    return result


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
