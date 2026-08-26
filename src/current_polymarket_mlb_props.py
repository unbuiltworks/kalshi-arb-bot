"""Cached discovery of current Polymarket US MLB player props.

The older direct PROP/search surfaces can return an empty player-prop inventory even
while the public sports event feed contains hundreds of strict MLB props. Discovery
therefore combines all three read-only surfaces and enriches event-feed markets with
their parent event timing/identity before applying the existing strict MLB parser.

Discovery is intentionally slow/cached; hot pricing uses exact market books only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Mapping

import httpx

from src.polymarket_mlb_shape_diagnostic import (
    _fetch_all_props,
    _fetch_search_events,
    _looks_like_player_prop,
)
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_schedule_guard import polymarket_start_utc

BASE = "https://gateway.polymarket.us"
_CACHE_ROWS: list[dict[str, Any]] = []
_CACHE_AT = 0.0
_CACHE_LOCK = asyncio.Lock()
DEFAULT_TTL_SECONDS = 180.0
EVENT_HORIZON_HOURS = 36
EVENT_MAX_PAGES = 12


def _enriched_search_markets(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_start = event.get("startTime") or event.get("start_time")
        event_slug = event.get("slug") or event.get("eventSlug")
        event_id = event.get("id")
        for raw in event.get("markets") or []:
            if not isinstance(raw, dict):
                continue
            market = dict(raw)
            if not _looks_like_player_prop(market) and _poly_identity(market) is None:
                continue
            if not market.get("gameStartTime") and not market.get("eventStartTime") and event_start:
                market["eventStartTime"] = event_start
            if not market.get("eventSlug") and event_slug:
                market["eventSlug"] = event_slug
            if not market.get("eventId") and event_id:
                market["eventId"] = event_id
            rows.append(market)
    return rows


async def _fetch_event_feed_props(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Read the working near-term sports event surface and retain strict MLB rows."""
    horizon = (datetime.now(timezone.utc) + timedelta(hours=EVENT_HORIZON_HOURS)).isoformat().replace("+00:00", "Z")
    events: list[dict[str, Any]] = []
    for page in range(EVENT_MAX_PAGES):
        response = await http.get(
            f"{BASE}/v1/events",
            params={
                "limit": 100,
                "offset": page * 100,
                "active": "true",
                "closed": "false",
                "ended": "false",
                "categories": "sports",
                "startTimeMax": horizon,
                "orderBy": "startTime",
                "orderDirection": "desc",
            },
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        page_events = body.get("events") or [] if isinstance(body, Mapping) else []
        for raw in page_events:
            if isinstance(raw, dict):
                events.append(raw)
        if len(page_events) < 100:
            break
    return _enriched_search_markets(events)


def _strict_ready(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Prefer the richest duplicate for each slug. Event/search rows often carry
    # parent timing and rule metadata that direct /v1/markets rows omit.
    by_slug: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _poly_identity(row) is None:
            continue
        slug = str(row.get("slug") or "")
        if not slug:
            continue
        current = by_slug.get(slug)
        score = sum(row.get(key) not in (None, "") for key in (
            "gameStartTime", "eventStartTime", "eventSlug", "eventId",
            "sportsMarketType", "sportsMarketTypeV2", "question", "title", "line",
            "orderPriceMinTickSize", "minimumTradeQty", "marketRules",
        ))
        current_score = -1 if current is None else sum(current.get(key) not in (None, "") for key in (
            "gameStartTime", "eventStartTime", "eventSlug", "eventId",
            "sportsMarketType", "sportsMarketTypeV2", "question", "title", "line",
            "orderPriceMinTickSize", "minimumTradeQty", "marketRules",
        ))
        if current is None or score > current_score:
            by_slug[slug] = dict(row)

    out: list[dict[str, Any]] = []
    for row in by_slug.values():
        if polymarket_start_utc(row) is None:
            continue
        out.append(row)
    out.sort(key=lambda row: str(row.get("slug") or ""))
    return out


async def fetch_current_poly_mlb_props(
    http: httpx.AsyncClient,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> list[dict[str, Any]]:
    global _CACHE_ROWS, _CACHE_AT
    now = time.monotonic()
    if _CACHE_ROWS and now - _CACHE_AT < ttl_seconds:
        return [dict(row) for row in _CACHE_ROWS]

    async with _CACHE_LOCK:
        now = time.monotonic()
        if _CACHE_ROWS and now - _CACHE_AT < ttl_seconds:
            return [dict(row) for row in _CACHE_ROWS]

        async def safe(coro: Any) -> list[dict[str, Any]]:
            try:
                rows = await coro
                return [dict(row) for row in rows if isinstance(row, dict)]
            except Exception:
                return []

        direct, search_events, event_rows = await asyncio.gather(
            safe(_fetch_all_props(http)),
            safe(_fetch_search_events(http)),
            safe(_fetch_event_feed_props(http)),
        )
        search_rows = _enriched_search_markets(search_events)
        rows = _strict_ready(list(direct) + search_rows + event_rows)

        # Fail closed but retain a recent non-empty cache through a transient empty
        # discovery response instead of erasing the hot set.
        if rows:
            _CACHE_ROWS = [dict(row) for row in rows]
            _CACHE_AT = time.monotonic()
        return [dict(row) for row in (_CACHE_ROWS if _CACHE_ROWS else rows)]


def cache_status() -> dict[str, Any]:
    return {
        "cached_props": len(_CACHE_ROWS),
        "cache_age_seconds": None if not _CACHE_AT else round(time.monotonic() - _CACHE_AT, 3),
        "ttl_seconds": DEFAULT_TTL_SECONDS,
        "event_horizon_hours": EVENT_HORIZON_HOURS,
        "event_max_pages": EVENT_MAX_PAGES,
    }
