"""Cached discovery of current Polymarket US MLB player props.

The league event endpoint does not always surface player props.  This module combines
Polymarket's public PROP inventory and search surfaces, then keeps only rows that the
existing strict MLB prop adapter can semantically parse. Discovery is intentionally
slow/cached; hot pricing should not page the global sports universe every cycle.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from src.polymarket_mlb_shape_diagnostic import (
    _fetch_all_props,
    _fetch_search_events,
    _looks_like_player_prop,
)
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_schedule_guard import polymarket_start_utc

_CACHE_ROWS: list[dict[str, Any]] = []
_CACHE_AT = 0.0
_CACHE_LOCK = asyncio.Lock()
DEFAULT_TTL_SECONDS = 180.0


def _enriched_search_markets(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_start = event.get("startTime")
        for raw in event.get("markets") or []:
            if not isinstance(raw, dict):
                continue
            market = dict(raw)
            if not _looks_like_player_prop(market):
                continue
            if not market.get("gameStartTime") and event_start:
                market["eventStartTime"] = event_start
            rows.append(market)
    return rows


def _strict_ready(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Prefer the richest duplicate for each slug. Search rows often carry the event
    # start that direct /v1/markets rows omit.
    by_slug: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _looks_like_player_prop(row):
            continue
        slug = str(row.get("slug") or "")
        if not slug:
            continue
        current = by_slug.get(slug)
        score = sum(row.get(key) not in (None, "") for key in (
            "gameStartTime", "eventStartTime", "sportsMarketType", "sportsMarketTypeV2",
            "question", "title", "line",
        ))
        current_score = -1 if current is None else sum(current.get(key) not in (None, "") for key in (
            "gameStartTime", "eventStartTime", "sportsMarketType", "sportsMarketTypeV2",
            "question", "title", "line",
        ))
        if current is None or score > current_score:
            by_slug[slug] = row

    out: list[dict[str, Any]] = []
    for row in by_slug.values():
        if _poly_identity(row) is None:
            continue
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
        direct, search_events = await asyncio.gather(
            _fetch_all_props(http),
            _fetch_search_events(http),
        )
        rows = _strict_ready(list(direct) + _enriched_search_markets(search_events))
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
    }
