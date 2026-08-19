"""Read-only probe for live Polymarket US MLB player-prop rule metadata.

This diagnostic performs public GET requests only. It emits the exact public fields
that may carry settlement/rule provenance so the rule-certification layer can be
backed by current instrument metadata rather than assumptions.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

from src.current_polymarket_mlb_props import fetch_current_poly_mlb_props

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
    return None


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        markets = await fetch_current_poly_mlb_props(http, ttl_seconds=0.0)

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

    print(
        json.dumps(
            {
                "status": "POLYMARKET_MLB_PROP_RULE_PROBE_SUMMARY",
                "current_props": len(markets),
                "emitted": emitted,
                "families_seen": families,
                "read_only": True,
                "order_entry": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
