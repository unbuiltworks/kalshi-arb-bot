"""Cached discovery of current Polymarket US MLB player props.

The league event endpoint does not always surface player props. This module combines
Polymarket's public PROP inventory and search surfaces, then keeps only rows that the
existing strict MLB prop adapter can semantically parse. Discovery is intentionally
slow/cached; hot pricing should not page the global sports universe every cycle.

Current Polymarket search ranking can omit player-prop events from broad MLB queries.
For the dedicated final-lag service we can safely improve recall by seeding search with
player names extracted from the *already strict-parsed* current Kalshi prop slate. The
result still has to pass the unchanged Polymarket identity parser and exact cross-venue
matcher, so a search hit alone never authorizes a counterpart or an order.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable

import httpx

from src.polymarket_mlb_shape_diagnostic import (
    _fetch_all_props,
    _fetch_search_events,
    _looks_like_player_prop,
)
from src.strict_mlb_prop_adapters import FAMILY_SERIES, _kalshi_identity, _poly_identity
from src.strict_mlb_prop_schedule_guard import polymarket_start_utc
from src.strict_mlb_prop_snapshot import POLY

logger = logging.getLogger("kalshi_arb.current_polymarket_mlb_props")

_CACHE_ROWS: list[dict[str, Any]] = []
_CACHE_AT = 0.0
_CACHE_LOCK = asyncio.Lock()
DEFAULT_TTL_SECONDS = 180.0
MAX_DYNAMIC_PLAYER_QUERIES = 80
DYNAMIC_SEARCH_CONCURRENCY = 4


def kalshi_seed_players(events: Iterable[dict[str, Any]]) -> list[str]:
    """Return unique player names from strictly parseable supported Kalshi props."""
    series_to_family = {series: family for family, series in FAMILY_SERIES.items()}
    names: dict[str, str] = {}
    for event in events:
        family = series_to_family.get(str(event.get("series_ticker") or ""))
        if family is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            parsed = _kalshi_identity(event, market, family)
            if parsed is None:
                continue
            player, _threshold = parsed
            clean = " ".join(str(player).split())
            key = clean.casefold()
            if clean and key not in names:
                names[key] = clean
    return sorted(names.values(), key=str.casefold)[:MAX_DYNAMIC_PLAYER_QUERIES]


def _normalized_queries(values: Iterable[str]) -> list[str]:
    out: dict[str, str] = {}
    for value in values:
        clean = " ".join(str(value or "").split())
        if len(clean) < 3:
            continue
        key = clean.casefold()
        if key not in out:
            out[key] = clean
    return sorted(out.values(), key=str.casefold)[:MAX_DYNAMIC_PLAYER_QUERIES]


async def _fetch_seeded_search_events(
    http: httpx.AsyncClient,
    player_queries: Iterable[str],
) -> list[dict[str, Any]]:
    """Fetch one bounded targeted search page per current Kalshi player.

    Search is control-plane only and cached for three minutes. Four-way concurrency
    keeps the expansion timely without creating an API burst. Individual query errors
    fail soft; every returned market still passes strict identity/schedule parsing.
    """
    queries = _normalized_queries(player_queries)
    if not queries:
        return []
    semaphore = asyncio.Semaphore(DYNAMIC_SEARCH_CONCURRENCY)

    async def one(query: str) -> list[dict[str, Any]]:
        async with semaphore:
            response: httpx.Response | None = None
            for attempt in range(3):
                try:
                    response = await http.get(
                        f"{POLY}/v1/search",
                        params={"query": query, "limit": 100, "status": "active", "page": 1},
                    )
                except httpx.HTTPError:
                    response = None
                if response is not None and response.status_code == 200:
                    try:
                        return [dict(row) for row in (response.json().get("events") or [])]
                    except (TypeError, ValueError):
                        return []
                if response is None or response.status_code != 429:
                    return []
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0.20 * (2 ** attempt)
                except (TypeError, ValueError):
                    delay = 0.20 * (2 ** attempt)
                await asyncio.sleep(max(0.05, min(delay, 1.0)))
            return []

    groups = await asyncio.gather(*(one(query) for query in queries))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for event in group:
            key = str(event.get("id") or event.get("slug") or "")
            if key and key not in seen:
                seen.add(key)
                out.append(event)
    logger.info(
        "POLY_MLB_DYNAMIC_PLAYER_SEARCH player_queries=%d search_events=%d max_queries=%d concurrency=%d",
        len(queries), len(out), MAX_DYNAMIC_PLAYER_QUERIES, DYNAMIC_SEARCH_CONCURRENCY,
    )
    return out


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
    player_queries: Iterable[str] = (),
) -> list[dict[str, Any]]:
    global _CACHE_ROWS, _CACHE_AT
    now = time.monotonic()
    if _CACHE_ROWS and now - _CACHE_AT < ttl_seconds:
        return [dict(row) for row in _CACHE_ROWS]

    async with _CACHE_LOCK:
        now = time.monotonic()
        if _CACHE_ROWS and now - _CACHE_AT < ttl_seconds:
            return [dict(row) for row in _CACHE_ROWS]
        direct, broad_search_events, seeded_search_events = await asyncio.gather(
            _fetch_all_props(http),
            _fetch_search_events(http),
            _fetch_seeded_search_events(http, player_queries),
        )
        rows = _strict_ready(
            list(direct)
            + _enriched_search_markets(broad_search_events)
            + _enriched_search_markets(seeded_search_events)
        )
        # Fail closed but retain a recent non-empty cache through a transient empty
        # discovery response instead of erasing the hot set.
        if rows:
            _CACHE_ROWS = [dict(row) for row in rows]
            _CACHE_AT = time.monotonic()
        logger.info(
            "POLY_MLB_STRICT_DISCOVERY direct=%d broad_search_events=%d seeded_search_events=%d strict_ready=%d",
            len(direct), len(broad_search_events), len(seeded_search_events), len(rows),
        )
        return [dict(row) for row in (_CACHE_ROWS if _CACHE_ROWS else rows)]


def cache_status() -> dict[str, Any]:
    return {
        "cached_props": len(_CACHE_ROWS),
        "cache_age_seconds": None if not _CACHE_AT else round(time.monotonic() - _CACHE_AT, 3),
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    }
