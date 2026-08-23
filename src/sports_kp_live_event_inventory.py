"""Read-only current Polymarket US sports-event inventory for the K/P lane.

This module deliberately performs no cross-venue counterpart certification or order
entry. It runs the bounded, mutation-free same-venue Polymarket MLB maker q1
preflight first so a flaky broad inventory page cannot block authenticated revenue
validation on startup.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Mapping

import httpx

POLY = "https://gateway.polymarket.us"
logger = logging.getLogger("kalshi_arb.sports_kp_live_event_inventory")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _league(event: Mapping[str, Any]) -> str:
    for key in ("league", "sportsLeague", "subcategory", "seriesSlug", "series"):
        value = event.get(key)
        if isinstance(value, Mapping):
            value = value.get("slug") or value.get("name") or value.get("title")
        text = _text(value)
        if text:
            return text.lower()
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        for key in ("league", "sportsLeague", "subcategory"):
            text = _text(market.get(key))
            if text:
                return text.lower()
    return "unknown"


def _market_type(market: Mapping[str, Any]) -> str:
    return _text(
        market.get("sportsMarketTypeV2")
        or market.get("sportsMarketType")
        or market.get("marketType")
        or "UNKNOWN"
    ).upper().removeprefix("SPORTS_MARKET_TYPE_")


async def fetch_current_sports_events(
    http: httpx.AsyncClient,
    *,
    horizon_hours: int = 48,
    max_pages: int = 12,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(max_pages):
        try:
            response = await http.get(
                f"{POLY}/v1/events",
                params={
                    "limit": 100,
                    "offset": page * 100,
                    "active": "true",
                    "closed": "false",
                    "categories": "sports",
                    "startTimeMax": horizon.isoformat().replace("+00:00", "Z"),
                    "orderBy": "startTime",
                    "orderDirection": "asc",
                },
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("SPORTS_POLY_INVENTORY_PAGE_ERROR page=%s error=%s", page, type(exc).__name__)
            break
        body = response.json() if response.content else {}
        rows = body.get("events") or [] if isinstance(body, Mapping) else []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            key = _text(row.get("id") or row.get("slug") or row.get("eventId") or row.get("title"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(row)
        if len(rows) < 100:
            break
    return out


def summarize(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    leagues: Counter[str] = Counter()
    market_types: Counter[str] = Counter()
    started = 0
    upcoming = 0
    total_markets = 0
    samples: list[dict[str, Any]] = []
    for event in events:
        league = _league(event)
        leagues[league] += 1
        start = _iso(event.get("startTime") or event.get("startDate"))
        if start is not None and start <= now:
            started += 1
        else:
            upcoming += 1
        markets = [m for m in (event.get("markets") or []) if isinstance(m, Mapping)]
        total_markets += len(markets)
        for market in markets:
            market_types[_market_type(market)] += 1
        if len(samples) < 30:
            samples.append({
                "title": _text(event.get("title") or event.get("name")),
                "slug": _text(event.get("slug") or event.get("id")),
                "league": league,
                "start_time": start.isoformat() if start else None,
                "market_count": len(markets),
                "market_types": sorted({_market_type(m) for m in markets}),
            })
    return {
        "events": len(events),
        "started_or_live_by_start_time": started,
        "upcoming_or_missing_start": upcoming,
        "markets": total_markets,
        "leagues": dict(leagues.most_common()),
        "market_types": dict(market_types.most_common()),
        "sample_events": samples,
        "read_only": True,
        "order_entry": False,
    }


async def run_once(http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    # Revenue preflight first; it fetches its own tightly scoped near-term feed.
    try:
        from src.sports_kp_poly_maker_q1_preflight import run_once as maker_preflight
        await maker_preflight(None)
    except Exception:
        logger.exception("SPORTS_KP_POLY_MAKER_Q1_PREFLIGHT_ERROR")

    owns = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        events = await fetch_current_sports_events(client)
        payload = summarize(events)
        encoded = json.dumps(payload, separators=(",", ":"), default=str)
        logger.info("SPORTS_POLY_LIVE_EVENT_INVENTORY %s", encoded)
        print("SPORTS_POLY_LIVE_EVENT_INVENTORY " + encoded, flush=True)
        return payload
    finally:
        if owns:
            await client.aclose()
