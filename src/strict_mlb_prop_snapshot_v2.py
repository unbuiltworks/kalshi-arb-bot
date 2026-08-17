"""Read-only MLB prop snapshot using Polymarket's official Search surface.

Polymarket US currently exposes MLB player props in the app and /v1/search event
results, while both the league nested-market endpoint and the filtered /v1/markets
surface can omit them.  This module changes discovery only: it resolves each nearby
MLB game through /v1/search, extracts the explicit player-prop families, then reuses
all existing strict identity, scheduled-time, book, fee, participation, and rule
gates from strict_mlb_prop_snapshot.

No credentials or order-entry path are added here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base


PAST_GRACE = timedelta(hours=2)
# Player-prop boards can be posted the evening before games that are 20-24 hours
# away.  The previous 18h window silently dropped those already-live boards (for
# example, Sunday night discovery of Monday evening games).  Discovery is read-only;
# downstream scheduled-time/participation/rule gates still decide whether a pair is
# actionable, so it is safe to keep a wider catalog here.
FUTURE_HORIZON = timedelta(hours=36)
SEARCH_CONCURRENCY = 6


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _nearby_event(event: dict[str, Any], now: datetime) -> bool:
    start = _parse_utc(event.get("startTime"))
    if start is None:
        return False
    return now - PAST_GRACE <= start <= now + FUTURE_HORIZON


def _player_prop_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    event_start = event.get("startTime")
    rows: list[dict[str, Any]] = []
    for market in event.get("markets") or []:
        family = str(market.get("sportsMarketType") or "")
        if family not in base.FAMILY_SERIES:
            continue
        row = dict(market)
        if not row.get("gameStartTime") and event_start:
            row["eventStartTime"] = event_start
        rows.append(row)
    return rows


async def _search_one_game(
    http: httpx.AsyncClient,
    *,
    event_slug: str,
    event_title: str,
    event_start: Any,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async with semaphore:
        body = await base._fetch_json(
            http,
            f"{base.POLY}/v1/search",
            params={
                "query": event_title or event_slug,
                "limit": 100,
                "status": "active",
                "page": 1,
            },
        )

    candidates = list(body.get("events") or [])
    exact = next(
        (event for event in candidates if str(event.get("slug") or "") == event_slug),
        None,
    )
    if exact is None:
        # Fail closed: never grab a similarly named game just because search ranked it.
        return []

    resolved = dict(exact)
    if not resolved.get("startTime") and event_start:
        resolved["startTime"] = event_start
    return _player_prop_rows(resolved)


async def _fetch_poly_mlb_search(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    league = await base._fetch_json(
        http,
        f"{base.POLY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    now = datetime.now(timezone.utc)
    nearby = [dict(event) for event in league.get("events") or [] if _nearby_event(dict(event), now)]

    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
    groups = await asyncio.gather(
        *(
            _search_one_game(
                http,
                event_slug=str(event.get("slug") or ""),
                event_title=str(event.get("title") or ""),
                event_start=event.get("startTime"),
                semaphore=semaphore,
            )
            for event in nearby
            if str(event.get("slug") or "")
        )
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            slug = str(row.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(row)
    return out


# base.run_once resolves this module global at runtime, so replacing discovery keeps
# every existing strict matcher/economics/rule gate intact.
base._fetch_poly_mlb = _fetch_poly_mlb_search


if __name__ == "__main__":
    base.main()
