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
from src.polymarket_mlb_search_identity_audit import audit_search_identity


PAST_GRACE = timedelta(hours=2)
FUTURE_HORIZON = timedelta(hours=18)
SEARCH_CONCURRENCY = 6


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


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


def _resolve_exact_search_event(
    body: dict[str, Any],
    *,
    event_slug: str,
    event_title: str,
    event_start: Any,
) -> dict[str, Any] | None:
    """Return the exact Search event only when slug *and* scheduled time verify.

    The league inventory is the anchor for the nearby game instance.  Search can return
    a stale/postponed document with the same slug, so exact slug equality alone is not
    enough for prop discovery.  Reuse the independent identity audit and require the
    strict-prop-supply state, which also proves Search reproduces the league start time
    within the audit's bounded tolerance.
    """
    audit = audit_search_identity(
        body,
        expected_event_slug=event_slug,
        expected_event_start_time=event_start,
        query=event_title or event_slug,
    )
    if audit.get("status") != "EXACT_EVENT_WITH_STRICT_PROP_SUPPLY":
        return None

    events = [row for row in (body.get("events") or []) if isinstance(row, dict)]
    exact = [row for row in events if str(row.get("slug") or "").strip() == event_slug]
    if len(exact) != 1:
        return None
    return dict(exact[0])


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

    exact = _resolve_exact_search_event(
        body,
        event_slug=event_slug,
        event_title=event_title,
        event_start=event_start,
    )
    if exact is None:
        # Fail closed: never grab a similarly named, stale, postponed, or time-ambiguous
        # game just because Search ranked it or reproduced the same slug.
        return []

    return _player_prop_rows(exact)


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
