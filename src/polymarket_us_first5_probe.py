"""Read-only Polymarket US MLB first-five inventory/rule probe.

Queries the same public gateway used by the production sports scanner and emits
only rule/identity fields for explicit first-five markets.  No authentication,
account state, order-book mutation, or order transport is imported.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Mapping

import httpx

from src.universal_three_venue_sports import fetch_polymarket_mlb

_FIRST5_RE = re.compile(
    r"(?:^|[-_\s])(?:f5|first[-_\s]?5|1st[-_\s]?5|first[-_\s]?five)(?:$|[-_\s])|"
    r"\b(?:first|1st)\s+(?:five|5)\s+innings?\b|"
    r"\binnings?\s+(?:1\s*[-–]\s*5|1\s+through\s+5)\b",
    re.I,
)


def _text(row: Mapping[str, Any]) -> str:
    return " | ".join(
        str(row.get(k) or "")
        for k in (
            "slug", "question", "title", "subtitle", "description",
            "spreadTotalSuffix", "sportsMarketType", "sportsMarketTypeV2",
        )
    )


def _safe_market(market: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "id", "slug", "question", "title", "subtitle", "description",
        "sportsMarketType", "sportsMarketTypeV2", "marketType", "line",
        "spreadTotalSuffix", "active", "closed", "archived", "gameStartTime",
    )
    return {key: market.get(key) for key in fields if key in market}


def _safe_event(event: Mapping[str, Any], markets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "startTime": event.get("startTime"),
        "period": event.get("period"),
        "live": event.get("live"),
        "ended": event.get("ended"),
        "gameId": event.get("gameId"),
        "sportradarGameId": event.get("sportradarGameId"),
        "markets": markets,
    }


async def probe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        events = await fetch_polymarket_mlb(http)

    matched_events: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    total_markets = 0
    for event in events:
        matches: list[dict[str, Any]] = []
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping) or not _FIRST5_RE.search(_text(market)):
                continue
            safe = _safe_market(market)
            matches.append(safe)
            total_markets += 1
            typ = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "UNKNOWN")
            type_counts[typ] = type_counts.get(typ, 0) + 1
        if matches:
            matched_events.append(_safe_event(event, matches))

    return {
        "mode": "POLYMARKET_US_MLB_FIRST5_RULE_PROBE",
        "gateway": "gateway.polymarket.us",
        "raw_mlb_events": len(events),
        "first5_events": len(matched_events),
        "first5_markets": total_markets,
        "market_type_counts": dict(sorted(type_counts.items())),
        "events": matched_events[:40],
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print("POLYMARKET_US_FIRST5 " + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
