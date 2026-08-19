"""Read-only probe for live MLB player-prop rule metadata.

This diagnostic performs public GET requests only. It emits exact public Polymarket
market descriptions plus Kalshi series rule fields so rule certification can be
backed by current contract metadata rather than assumptions.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

from src.current_polymarket_mlb_props import fetch_current_poly_mlb_props

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_SERIES = ("KXMLBHIT", "KXMLBHRR", "KXMLBTB", "KXMLBKS")
INTERESTING_TOKENS = (
    "rule",
    "resolution",
    "settle",
    "source",
    "provider",
    "stat",
    "participant",
    "player",
    "subject",
    "expiration",
    "cancel",
    "postpone",
    "suspend",
    "shorten",
    "correction",
)


def _interesting(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            key_s = str(key)
            lowered = key_s.lower()
            if any(token in lowered for token in INTERESTING_TOKENS):
                out[key_s] = child
            elif isinstance(child, Mapping):
                nested = _interesting(child)
                if nested:
                    out[key_s] = nested
        return out
    if isinstance(value, list):
        rows = [_interesting(child) for child in value]
        return [row for row in rows if row]
    return None


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        markets = await fetch_current_poly_mlb_props(http, ttl_seconds=0.0)
        kalshi_responses = await asyncio.gather(
            *(http.get(f"{KALSHI}/series/{series}") for series in KALSHI_SERIES)
        )

    emitted = 0
    families: dict[str, int] = {}
    for market in markets:
        family = str(market.get("sportsMarketType") or market.get("sportsMarketTypeV2") or "")
        families[family] = families.get(family, 0) + 1
        payload = {
            "status": "POLYMARKET_MLB_PROP_RULE_FINGERPRINT",
            "family": family,
            "slug": market.get("slug"),
            "question": market.get("question") or market.get("title"),
            "description": market.get("description"),
            "gameStartTime": market.get("gameStartTime") or market.get("eventStartTime"),
            "market_keys": sorted(str(key) for key in market.keys()),
            "rule_fields": _interesting(market),
        }
        print(json.dumps(payload, sort_keys=True, default=str), flush=True)
        emitted += 1
        if emitted >= 30:
            break

    for series, response in zip(KALSHI_SERIES, kalshi_responses):
        response.raise_for_status()
        body = response.json()
        print(
            json.dumps(
                {
                    "status": "KALSHI_MLB_PROP_SERIES_RULE_FINGERPRINT",
                    "series": series,
                    "top_level_keys": sorted(str(key) for key in body.keys()),
                    "rule_fields": _interesting(body),
                    "body": body,
                },
                sort_keys=True,
                default=str,
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "status": "MLB_PROP_RULE_PROBE_SUMMARY",
                "current_polymarket_props": len(markets),
                "polymarket_emitted": emitted,
                "polymarket_families_seen": families,
                "kalshi_series_emitted": len(KALSHI_SERIES),
                "read_only": True,
                "order_entry": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
