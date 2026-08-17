"""Read-only MLB prop snapshot using Polymarket's official Search surface.

Polymarket US currently exposes MLB player props in the app and /v1/search event
results, while both the league nested-market endpoint and the filtered /v1/markets
surface can omit them. This module changes discovery only: it resolves each nearby
MLB game through /v1/search, extracts strictly corroborated player-prop families,
then reuses all existing strict identity, scheduled-time, book, fee, participation,
and rule gates from strict_mlb_prop_snapshot.

The Search response can omit sportsMarketType/sportsMarketTypeV2 on otherwise
fully-specified player props. In that case we normalize the row as a PROP only when
the MLB slug and parsed market text independently resolve to the same supported stat
family. The base strict identity matcher still re-checks teams, date, line/threshold,
and any slug threshold before a candidate can survive.

No credentials or order-entry path are added here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src import strict_mlb_prop_adapters as adapters
from src import strict_mlb_prop_snapshot as base


PAST_GRACE = timedelta(hours=2)
FUTURE_HORIZON = timedelta(hours=18)
SEARCH_CONCURRENCY = 6
PROP_TYPES = {"PROP", "SPORTS_MARKET_TYPE_PROP"}


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


def _normalize_search_prop_row(market: dict[str, Any], event_start: Any) -> dict[str, Any] | None:
    """Return one strictly validated search-market row or None.

    Polymarket Search sometimes drops both classification fields. We do not infer
    PROP merely from one signal. Missing metadata is repaired only when the MLB slug
    family and the independently parsed title/question family agree. The existing
    strict adapter then validates the complete identity, including line, teams and
    any encoded slug threshold. Explicit conflicting metadata is never overwritten.
    """
    row = dict(market)
    if not row.get("gameStartTime") and event_start:
        row["eventStartTime"] = event_start

    detailed_family = str(row.get("sportsMarketType") or "")
    prop_type = str(row.get("sportsMarketTypeV2") or "").upper()

    if detailed_family:
        if detailed_family not in base.FAMILY_SERIES:
            return None
    elif prop_type:
        if prop_type not in PROP_TYPES:
            return None
    else:
        slug = str(row.get("slug") or "")
        slug_family = adapters._family_from_slug(slug)
        parsed = adapters._parse_poly_text(row.get("question") or row.get("title"))
        if slug_family is None or parsed is None:
            return None
        _player, _threshold, text_family, _teams = parsed
        if slug_family != text_family:
            return None
        # This is a local normalization marker, not venue-supplied evidence. The
        # strict identity function below still re-checks every semantic component.
        row["sportsMarketTypeV2"] = "SPORTS_MARKET_TYPE_PROP"

    if adapters._poly_identity(row) is None:
        return None
    return row


def _player_prop_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    event_start = event.get("startTime")
    rows: list[dict[str, Any]] = []
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        row = _normalize_search_prop_row(market, event_start)
        if row is not None:
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
