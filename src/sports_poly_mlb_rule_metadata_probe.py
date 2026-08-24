"""Read-only raw Polymarket US metadata audit for strict MLB ladder candidates.

Fetches the current public sports event universe and direct market endpoints for a
small representative set of MLB prop slugs.  Emits parent-event identity plus all
rule/resolution/participation-ish market fields without authentication or mutation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

import httpx

BASE = "https://gateway.polymarket.us"
TARGETS = (
    "astatc-mlb-tb-det-2026-08-24-tb-yandia-gte3",
    "astatc-mlb-tb-det-2026-08-24-tb-yandia-gte4",
    "astatc-mlb-tex-cws-2026-08-24-k-kumroc-gte4",
    "astatc-mlb-tex-cws-2026-08-24-k-kumroc-gte5",
    "astatc-mlb-tex-cws-2026-08-24-hits-branim-gte2",
    "astatc-mlb-tex-cws-2026-08-24-hits-branim-gte3",
)
INTEREST = (
    "rule", "resolution", "resolve", "settle", "void", "cancel", "postpon",
    "source", "term", "participant", "player", "subject", "team", "game",
    "event", "title", "question", "line", "sportsmarket", "status", "active",
    "closed", "accept", "start", "end", "outcome", "token", "description",
)


def compact(value: Any, limit: int = 12000) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())[:limit]
    if isinstance(value, list):
        return [compact(v, limit=3000) for v in value[:30]]
    if isinstance(value, Mapping):
        return {str(k): compact(v, limit=3000) for k, v in list(value.items())[:80]}
    return value


def interesting(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        low = str(key).lower()
        if any(token in low for token in INTEREST):
            out[str(key)] = compact(value)
    return out


async def main() -> None:
    target_set = set(TARGETS)
    found: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    horizon = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat().replace("+00:00", "Z")

    async with httpx.AsyncClient(timeout=20.0) as http:
        for offset in range(0, 5000, 100):
            r = await http.get(f"{BASE}/v1/events", params={
                "limit": 100,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "ended": "false",
                "categories": "sports",
                "startTimeMax": horizon,
                "orderBy": "startTime",
                "orderDirection": "desc",
            })
            r.raise_for_status()
            body = r.json() if r.content else {}
            events = body.get("events") or [] if isinstance(body, Mapping) else []
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                for market in event.get("markets") or []:
                    if not isinstance(market, Mapping):
                        continue
                    slug = str(market.get("slug") or "")
                    if slug in target_set:
                        found[slug] = (event, market)
            if target_set.issubset(found) or len(events) < 100:
                break

        direct: dict[str, dict[str, Any]] = {}
        for slug in TARGETS:
            for url in (f"{BASE}/v1/markets/{slug}", f"{BASE}/v1/markets/{slug}/book"):
                try:
                    r = await http.get(url)
                    payload: Any
                    try:
                        payload = r.json()
                    except Exception:
                        payload = r.text[:2000]
                    direct[url] = {
                        "status": r.status_code,
                        "body_keys": sorted(str(k) for k in payload.keys()) if isinstance(payload, Mapping) else [],
                        "interesting": interesting(payload) if isinstance(payload, Mapping) else compact(payload),
                    }
                except Exception as exc:
                    direct[url] = {"error": f"{type(exc).__name__}: {str(exc)[:500]}"}

    for slug in TARGETS:
        event, market = found.get(slug, ({}, {}))
        print("SPORTS_POLY_MLB_RAW_METADATA " + json.dumps({
            "slug": slug,
            "found_in_event_feed": bool(market),
            "event_keys": sorted(str(k) for k in event.keys()) if isinstance(event, Mapping) else [],
            "event_interesting": interesting(event) if isinstance(event, Mapping) else {},
            "market_keys": sorted(str(k) for k in market.keys()) if isinstance(market, Mapping) else [],
            "market_interesting": interesting(market) if isinstance(market, Mapping) else {},
            "direct_market": direct.get(f"{BASE}/v1/markets/{slug}"),
            "direct_book": direct.get(f"{BASE}/v1/markets/{slug}/book"),
            "read_only": True,
            "order_entry": False,
        }, separators=(",",":"), default=str), flush=True)

    print("SPORTS_POLY_MLB_RAW_METADATA_SUMMARY " + json.dumps({
        "targets": len(TARGETS),
        "found": len(found),
        "missing": sorted(target_set - set(found)),
        "read_only": True,
        "order_entry": False,
    }, separators=(",",":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
