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
import json
import logging
from typing import Any

import httpx

from src import strict_mlb_prop_adapters as adapters
from src import strict_mlb_prop_snapshot as base


logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v2")
PAST_GRACE = timedelta(hours=2)
FUTURE_HORIZON = timedelta(hours=18)
SEARCH_CONCURRENCY = 6
PROP_TYPES = {"PROP", "SPORTS_MARKET_TYPE_PROP"}
DIAGNOSTIC_SAMPLE_LIMIT = 8


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


def _market_diag(market: dict[str, Any], reason: str) -> dict[str, Any]:
    """Return only non-secret market identity fields for diagnostic logging."""
    return {
        "reason": reason,
        "slug": str(market.get("slug") or "")[:220],
        "title": str(market.get("title") or "")[:220],
        "question": str(market.get("question") or "")[:220],
        "line": market.get("line"),
        "sportsMarketType": market.get("sportsMarketType"),
        "sportsMarketTypeV2": market.get("sportsMarketTypeV2"),
        "gameStartTime": market.get("gameStartTime"),
        "eventStartTime": market.get("eventStartTime"),
    }


def _candidate_looking(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(key) or "").lower()
        for key in ("slug", "title", "question", "sportsMarketType", "sportsMarketTypeV2")
    )
    return "mlb" in text and any(
        token in text
        for token in (
            "hit",
            "hrr",
            "rbi",
            "total base",
            "-tb-",
            "strikeout",
            "-so-",
            "-k-",
            "prop",
        )
    )


def _normalize_search_prop_row_with_reason(
    market: dict[str, Any], event_start: Any
) -> tuple[dict[str, Any] | None, str]:
    row = dict(market)
    if not row.get("gameStartTime") and event_start:
        row["eventStartTime"] = event_start

    detailed_family = str(row.get("sportsMarketType") or "")
    prop_type = str(row.get("sportsMarketTypeV2") or "").upper()

    if detailed_family:
        if detailed_family not in base.FAMILY_SERIES:
            return None, "EXPLICIT_DETAILED_TYPE_UNSUPPORTED"
    elif prop_type:
        if prop_type not in PROP_TYPES:
            return None, "EXPLICIT_V2_TYPE_NOT_PROP"
    else:
        slug = str(row.get("slug") or "")
        slug_family = adapters._family_from_slug(slug)
        parsed = adapters._parse_poly_text(row.get("question") or row.get("title"))
        if slug_family is None:
            return None, "MISSING_TYPES_SLUG_FAMILY_UNPARSED"
        if parsed is None:
            return None, "MISSING_TYPES_TEXT_UNPARSED"
        _player, _threshold, text_family, _teams = parsed
        if slug_family != text_family:
            return None, "MISSING_TYPES_SLUG_TEXT_FAMILY_DISAGREE"
        # This is a local normalization marker, not venue-supplied evidence. The
        # strict identity function below still re-checks every semantic component.
        row["sportsMarketTypeV2"] = "SPORTS_MARKET_TYPE_PROP"

    if adapters._poly_identity(row) is None:
        return None, "STRICT_POLY_IDENTITY_REJECTED"
    return row, "ACCEPTED"


def _normalize_search_prop_row(market: dict[str, Any], event_start: Any) -> dict[str, Any] | None:
    """Return one strictly validated search-market row or None."""
    row, _reason = _normalize_search_prop_row_with_reason(market, event_start)
    return row


def _player_prop_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    event_start = event.get("startTime")
    rows: list[dict[str, Any]] = []
    reject_counts: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    raw_markets = event.get("markets") or []
    for market in raw_markets:
        if not isinstance(market, dict):
            reject_counts["NON_MAPPING_MARKET"] = reject_counts.get("NON_MAPPING_MARKET", 0) + 1
            continue
        row, reason = _normalize_search_prop_row_with_reason(market, event_start)
        if row is not None:
            rows.append(row)
            continue
        reject_counts[reason] = reject_counts.get(reason, 0) + 1
        if len(samples) < DIAGNOSTIC_SAMPLE_LIMIT and _candidate_looking(market):
            samples.append(_market_diag(market, reason))

    logger.info(
        "MLB_PROP_SEARCH_MARKET_DIAG %s",
        json.dumps(
            {
                "event_slug": event.get("slug"),
                "event_title": event.get("title"),
                "event_start": event_start,
                "raw_market_count": len(raw_markets),
                "accepted_count": len(rows),
                "reject_counts": reject_counts,
                "candidate_looking_reject_samples": samples,
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
            default=str,
        ),
    )
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

    candidates = [event for event in body.get("events") or [] if isinstance(event, dict)]
    exact = next(
        (event for event in candidates if str(event.get("slug") or "") == event_slug),
        None,
    )
    logger.info(
        "MLB_PROP_SEARCH_EVENT_DIAG %s",
        json.dumps(
            {
                "league_event_slug": event_slug,
                "league_event_title": event_title,
                "league_event_start": event_start,
                "search_event_count": len(candidates),
                "search_event_slugs": [str(event.get("slug") or "")[:180] for event in candidates[:12]],
                "search_event_titles": [str(event.get("title") or "")[:180] for event in candidates[:12]],
                "exact_slug_match": exact is not None,
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
            default=str,
        ),
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
    nearby = [dict(event) for event in league.get("events") or [] if isinstance(event, dict) and _nearby_event(dict(event), now)]
    logger.info(
        "MLB_PROP_NEARBY_EVENTS %s",
        json.dumps(
            {
                "now": now.isoformat(),
                "nearby_count": len(nearby),
                "nearby": [
                    {
                        "slug": event.get("slug"),
                        "title": event.get("title"),
                        "startTime": event.get("startTime"),
                    }
                    for event in nearby[:40]
                ],
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
            default=str,
        ),
    )

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
    logger.info(
        "MLB_PROP_SEARCH_DISCOVERY_SUMMARY %s",
        json.dumps(
            {
                "nearby_event_count": len(nearby),
                "strict_poly_rows": len(out),
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
        ),
    )
    return out


# base.run_once resolves this module global at runtime, so replacing discovery keeps
# every existing strict matcher/economics/rule gate intact.
base._fetch_poly_mlb = _fetch_poly_mlb_search


if __name__ == "__main__":
    base.main()
