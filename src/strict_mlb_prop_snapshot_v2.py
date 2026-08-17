"""Read-only MLB prop snapshot using Polymarket's official Search surface.

Discovery intentionally uses only public GET endpoints and strict semantic matching.
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
MARKET_START_TOLERANCE_SECONDS = 300


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
        for token in ("hit", "hrr", "rbi", "total base", "-tb-", "strikeout", "-so-", "-k-", "prop")
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
        row["sportsMarketTypeV2"] = "SPORTS_MARKET_TYPE_PROP"

    if adapters._poly_identity(row) is None:
        return None, "STRICT_POLY_IDENTITY_REJECTED"
    return row, "ACCEPTED"


def _normalize_search_prop_row(market: dict[str, Any], event_start: Any) -> dict[str, Any] | None:
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
            }, separators=(",", ":"), default=str,
        ),
    )
    return rows


def _market_belongs_to_event(market: dict[str, Any], event_slug: str, event_start: Any) -> bool:
    target_tuple = adapters._poly_game_tuple({"slug": event_slug})
    market_tuple = adapters._poly_game_tuple(market)
    if target_tuple is None or market_tuple != target_tuple:
        return False
    target_start = _parse_utc(event_start)
    market_start = _parse_utc(market.get("gameStartTime") or market.get("eventStartTime") or market.get("startTime"))
    if target_start is None or market_start is None:
        # Same-day doubleheaders make missing start time ambiguous, so fail closed.
        return False
    return abs((target_start - market_start).total_seconds()) <= MARKET_START_TOLERANCE_SECONDS


async def _search_one_game(
    http: httpx.AsyncClient,
    *,
    event_slug: str,
    event_title: str,
    event_start: Any,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async with semaphore:
        event_body, market_body = await asyncio.gather(
            base._fetch_json(
                http,
                f"{base.POLY}/v1/search",
                params={"query": event_title or event_slug, "limit": 100, "status": "active", "page": 1},
            ),
            base._fetch_json(
                http,
                f"{base.POLY}/v1/search",
                params={"query": event_title or event_slug, "limit": 100, "active": "true", "page": 1},
            ),
        )

    candidates = [event for event in event_body.get("events") or [] if isinstance(event, dict)]
    active_markets = [market for market in market_body.get("markets") or [] if isinstance(market, dict)]
    logger.info(
        "MLB_PROP_ACTIVE_SEARCH_SURFACE %s",
        json.dumps(
            {
                "league_event_slug": event_slug,
                "body_keys": sorted(str(key) for key in market_body.keys()),
                "market_count": len(active_markets),
                "market_samples": [_market_diag(market, "ACTIVE_TRUE_SEARCH") for market in active_markets[:12]],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )

    exact = next((event for event in candidates if str(event.get("slug") or "") == event_slug), None)
    logger.info(
        "MLB_PROP_SEARCH_EVENT_DIAG %s",
        json.dumps(
            {
                "league_event_slug": event_slug,
                "league_event_title": event_title,
                "league_event_start": event_start,
                "search_event_count": len(candidates),
                "search_event_slugs": [str(event.get("slug") or "")[:180] for event in candidates[:12]],
                "exact_slug_match": exact is not None,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )

    rows: list[dict[str, Any]] = []
    if exact is not None:
        resolved = dict(exact)
        if not resolved.get("startTime") and event_start:
            resolved["startTime"] = event_start
        rows.extend(_player_prop_rows(resolved))

    active_accepts: list[dict[str, Any]] = []
    active_rejects: dict[str, int] = {}
    active_samples: list[dict[str, Any]] = []
    for market in active_markets:
        if not _market_belongs_to_event(market, event_slug, event_start):
            continue
        row, reason = _normalize_search_prop_row_with_reason(market, event_start)
        if row is not None:
            active_accepts.append(row)
        else:
            active_rejects[reason] = active_rejects.get(reason, 0) + 1
            if len(active_samples) < DIAGNOSTIC_SAMPLE_LIMIT:
                active_samples.append(_market_diag(market, reason))
    rows.extend(active_accepts)
    logger.info(
        "MLB_PROP_ACTIVE_SEARCH_FILTER %s",
        json.dumps(
            {
                "league_event_slug": event_slug,
                "same_event_market_count": len(active_accepts) + sum(active_rejects.values()),
                "accepted_count": len(active_accepts),
                "reject_counts": active_rejects,
                "reject_samples": active_samples,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )
    return rows


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
                "nearby": [{"slug": e.get("slug"), "title": e.get("title"), "startTime": e.get("startTime")} for e in nearby[:40]],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
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
            for event in nearby if str(event.get("slug") or "")
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
        json.dumps({"nearby_event_count": len(nearby), "strict_poly_rows": len(out), "read_only": True, "order_entry": False}, separators=(",", ":")),
    )
    return out


base._fetch_poly_mlb = _fetch_poly_mlb_search


if __name__ == "__main__":
    base.main()
