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

POLY = "https://gateway.polymarket.us"
PROP_FAMILIES = {
    "baseball_player_hits",
    "baseball_player_hits_runs_rbis",
    "baseball_player_total_bases",
    "baseball_player_strikeouts",
}
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
        response = await http.get(
            f"{POLY}/v2/leagues/mlb/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
        response.raise_for_status()
        body = response.json()

    emitted = 0
    families: dict[str, int] = {}
    for event in body.get("events") or []:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            family = str(market.get("sportsMarketType") or "")
            if family not in PROP_FAMILIES:
                continue
            families[family] = families.get(family, 0) + 1
            payload = {
                "status": "POLYMARKET_MLB_PROP_RULE_FINGERPRINT",
                "family": family,
                "slug": market.get("slug"),
                "question": market.get("question") or market.get("title"),
                "description": market.get("description"),
                "gameStartTime": market.get("gameStartTime") or event.get("startTime"),
                "market_keys": sorted(str(key) for key in market.keys()),
                "event_keys": sorted(str(key) for key in event.keys()),
                "rule_fields": _interesting(market),
                "event_rule_fields": _interesting(event),
            }
            print(json.dumps(payload, sort_keys=True, default=str), flush=True)
            emitted += 1
            if emitted >= 20:
                break
        if emitted >= 20:
            break

    print(
        json.dumps(
            {
                "status": "POLYMARKET_MLB_PROP_RULE_PROBE_SUMMARY",
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
