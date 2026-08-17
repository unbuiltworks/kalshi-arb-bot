"""Read-only diagnostic for current MLB prop discovery surfaces.

Inspects the live Polymarket league/search shapes and the strict identity adapter so
we can see exactly where today's candidate pipeline becomes empty. No credentials or
order-entry code are used.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v2 as v2
from src.strict_mlb_prop_adapters import _poly_identity


def _market_summary(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": m.get("slug"),
        "title": m.get("title"),
        "question": m.get("question"),
        "sportsMarketType": m.get("sportsMarketType"),
        "sportsMarketTypeV2": m.get("sportsMarketTypeV2"),
        "line": m.get("line"),
        "gameStartTime": m.get("gameStartTime"),
        "eventStartTime": m.get("eventStartTime"),
        "strict_identity": _poly_identity(m),
    }


def _event_summary(e: dict[str, Any]) -> dict[str, Any]:
    markets = [dict(m) for m in e.get("markets") or []]
    strict = [m for m in markets if _poly_identity(m) is not None]
    return {
        "slug": e.get("slug"),
        "title": e.get("title"),
        "startTime": e.get("startTime"),
        "market_count": len(markets),
        "sportsMarketType_counts": dict(Counter(str(m.get("sportsMarketType") or "<missing>") for m in markets)),
        "sportsMarketTypeV2_counts": dict(Counter(str(m.get("sportsMarketTypeV2") or "<missing>") for m in markets)),
        "strict_identity_count": len(strict),
        "strict_identity_samples": [_market_summary(m) for m in strict[:25]],
        "market_samples": [_market_summary(m) for m in markets[:15]],
    }


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        league = await base._fetch_json(
            http,
            f"{base.POLY}/v2/leagues/mlb/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
        now = datetime.now(timezone.utc)
        all_events = [dict(e) for e in league.get("events") or []]
        nearby = [e for e in all_events if v2._nearby_event(e, now)]

        event_rows: list[dict[str, Any]] = []
        extracted_rows: list[dict[str, Any]] = []
        for event in nearby[:30]:
            slug = str(event.get("slug") or "")
            title = str(event.get("title") or "")
            body = await base._fetch_json(
                http,
                f"{base.POLY}/v1/search",
                params={"query": title or slug, "limit": 100, "status": "active", "page": 1},
            )
            results = [dict(x) for x in body.get("events") or []]
            exact = next((x for x in results if str(x.get("slug") or "") == slug), None)
            markets = [dict(m) for m in (exact or {}).get("markets") or []]
            propish = [m for m in markets if "PROP" in str(m.get("sportsMarketTypeV2") or "").upper() or "player" in str(m.get("sportsMarketType") or "").lower()]
            strict = [m for m in markets if _poly_identity(m) is not None]
            event_rows.append({
                "league_slug": slug,
                "league_title": title,
                "league_start": event.get("startTime"),
                "search_events": len(results),
                "search_result_events": [_event_summary(x) for x in results[:20]],
                "exact_slug_found": exact is not None,
                "search_market_count": len(markets),
                "sportsMarketType_counts": dict(Counter(str(m.get("sportsMarketType") or "<missing>") for m in markets)),
                "sportsMarketTypeV2_counts": dict(Counter(str(m.get("sportsMarketTypeV2") or "<missing>") for m in markets)),
                "propish_market_count": len(propish),
                "strict_identity_count": len(strict),
                "market_samples": [_market_summary(m) for m in markets[:25]],
                "propish_samples": [_market_summary(m) for m in propish[:25]],
            })
            if exact is not None:
                resolved = dict(exact)
                if not resolved.get("startTime") and event.get("startTime"):
                    resolved["startTime"] = event.get("startTime")
                extracted_rows.extend(v2._player_prop_rows(resolved))

        kalshi_events = await base._fetch_kalshi_events(http)
        strict_candidates = base.discover_strict_candidates(extracted_rows, kalshi_events)

    result = {
        "checked_at_utc": now.isoformat(),
        "league_event_count": len(all_events),
        "nearby_event_count": len(nearby),
        "nearby_events": event_rows,
        "extracted_player_prop_rows": len(extracted_rows),
        "extracted_strict_poly_identities": sum(_poly_identity(m) is not None for m in extracted_rows),
        "kalshi_event_count": len(kalshi_events),
        "strict_candidate_count": len(strict_candidates),
        "strict_candidates": [c.__dict__ for c in strict_candidates[:50]],
        "read_only": True,
        "order_entry": False,
    }
    print("MLB_PROP_DISCOVERY_DIAG " + json.dumps(result, default=str, separators=(",", ":")))
    return result


if __name__ == "__main__":
    asyncio.run(run())
