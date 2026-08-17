"""Read-only MLB prop discovery for Polymarket US <-> Kalshi.

The game-level Polymarket search surface does not always nest player props. Discovery
therefore uses two independent public-GET paths:

1. nearby MLB game search, for any player props that are nested under the game; and
2. Kalshi-led player search: derive current player/family/threshold/start identities
   from open Kalshi MLB prop events, search Polymarket by those player names, and keep
   only rows whose strict semantic identity and scheduled start agree with an actual
   Kalshi target.

The second path is discovery only. The base scanner still performs the complete
rule-audit, exact counterpart, book, fee, participation, and schedule gates before a
pair can become paper evidence. No credentials or order-entry path exist here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from typing import Any

import httpx

from src import strict_mlb_prop_adapters as adapters
from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_schedule_guard import (
    MAX_START_SKEW_SECONDS,
    kalshi_event_start_utc,
    polymarket_start_utc,
)


logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v2")
PAST_GRACE = timedelta(hours=2)
FUTURE_HORIZON = timedelta(hours=18)
SEARCH_CONCURRENCY = 6
PROP_TYPES = {"PROP", "SPORTS_MARKET_TYPE_PROP"}
DIAGNOSTIC_SAMPLE_LIMIT = 8
MAX_PLAYER_SEARCHES = 36
DISCOVERY_CACHE_SECONDS = 300

_DISCOVERY_CACHE_AT: datetime | None = None
_DISCOVERY_CACHE_ROWS: list[dict[str, Any]] = []


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _nearby_start(start: datetime | None, now: datetime) -> bool:
    return start is not None and now - PAST_GRACE <= start <= now + FUTURE_HORIZON


def _nearby_event(event: dict[str, Any], now: datetime) -> bool:
    return _nearby_start(_parse_utc(event.get("startTime")), now)


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


def _player_prop_rows(event: dict[str, Any], *, log_diag: bool = True) -> list[dict[str, Any]]:
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
    if log_diag:
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
            params={"query": event_title or event_slug, "limit": 100, "status": "active", "page": 1},
        )
    candidates = [event for event in body.get("events") or [] if isinstance(event, dict)]
    exact = next((event for event in candidates if str(event.get("slug") or "") == event_slug), None)
    if exact is None:
        return []
    resolved = dict(exact)
    if not resolved.get("startTime") and event_start:
        resolved["startTime"] = event_start
    return _player_prop_rows(resolved)


async def _fetch_kalshi_player_targets(
    http: httpx.AsyncClient, now: datetime
) -> dict[str, dict[str, Any]]:
    """Return near-term, exact player identities already listed by Kalshi."""
    series_to_family = {series: family for family, series in base.FAMILY_SERIES.items()}
    series = sorted(series_to_family)
    bodies = await asyncio.gather(
        *(
            base._fetch_json(
                http,
                f"{base.KALSHI}/events",
                params={
                    "limit": 200,
                    "status": "open",
                    "series_ticker": ticker,
                    "with_nested_markets": "true",
                },
            )
            for ticker in series
        )
    )

    by_player: dict[str, dict[str, Any]] = {}
    total_identity_valid = 0
    total_near_term = 0
    for ticker, body in zip(series, bodies):
        family = series_to_family[ticker]
        for event in body.get("events") or []:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                identity = adapters._kalshi_identity(event, market, family)
                if identity is None:
                    continue
                total_identity_valid += 1
                start = kalshi_event_start_utc(event, market)
                if not _nearby_start(start, now):
                    continue
                total_near_term += 1
                player, threshold = identity
                key = adapters._name(player)
                if not key:
                    continue
                bucket = by_player.setdefault(
                    key,
                    {"player": player, "targets": [], "earliest_start": start},
                )
                if start is not None and (bucket["earliest_start"] is None or start < bucket["earliest_start"]):
                    bucket["earliest_start"] = start
                target_key = (family, str(threshold), start.isoformat() if start else "")
                if target_key not in {
                    (t["family"], t["threshold"], t["start_utc"]) for t in bucket["targets"]
                }:
                    bucket["targets"].append(
                        {
                            "family": family,
                            "threshold": str(threshold),
                            "start_utc": start.isoformat() if start else "",
                            "event_ticker": event.get("event_ticker") or market.get("event_ticker"),
                        }
                    )

    ordered = sorted(
        by_player.items(),
        key=lambda item: (item[1]["earliest_start"] or datetime.max.replace(tzinfo=timezone.utc), item[1]["player"]),
    )[:MAX_PLAYER_SEARCHES]
    selected = dict(ordered)
    logger.info(
        "MLB_PROP_KALSHI_PLAYER_TARGETS %s",
        json.dumps(
            {
                "identity_valid_markets": total_identity_valid,
                "near_term_identity_markets": total_near_term,
                "unique_near_term_players": len(by_player),
                "selected_player_searches": len(selected),
                "sample": [
                    {
                        "player": data["player"],
                        "targets": data["targets"][:4],
                    }
                    for data in list(selected.values())[:12]
                ],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )
    return selected


def _matches_kalshi_target(row: dict[str, Any], target: dict[str, Any]) -> bool:
    identity = adapters._poly_identity(row)
    if identity is None:
        return False
    family, player, _teams, _date, threshold = identity
    if adapters._name(player) != adapters._name(target["player"]):
        return False
    poly_start = polymarket_start_utc(row)
    if poly_start is None:
        return False
    for candidate in target["targets"]:
        if family != candidate["family"]:
            continue
        if threshold != Decimal(candidate["threshold"]):
            continue
        target_start = _parse_utc(candidate["start_utc"])
        if target_start is None:
            continue
        if abs((poly_start - target_start).total_seconds()) <= MAX_START_SKEW_SECONDS:
            return True
    return False


async def _search_one_player(
    http: httpx.AsyncClient,
    *,
    target: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async with semaphore:
        body = await base._fetch_json(
            http,
            f"{base.POLY}/v1/search",
            params={"query": target["player"], "limit": 100, "status": "active", "page": 1},
        )
    events = [event for event in body.get("events") or [] if isinstance(event, dict)]
    rows: list[dict[str, Any]] = []
    raw_market_count = 0
    strict_player_rows = 0
    for event in events:
        raw_market_count += len(event.get("markets") or [])
        for row in _player_prop_rows(event, log_diag=False):
            strict_player_rows += 1
            if _matches_kalshi_target(row, target):
                rows.append(row)
    logger.info(
        "MLB_PROP_PLAYER_SEARCH_DIAG %s",
        json.dumps(
            {
                "player": target["player"],
                "search_events": len(events),
                "search_nested_markets": raw_market_count,
                "strict_player_prop_rows": strict_player_rows,
                "exact_current_counterparts": len(rows),
                "accepted_samples": [_market_diag(row, "EXACT_CURRENT_COUNTERPART") for row in rows[:5]],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )
    return rows


async def _fetch_poly_mlb_search(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    global _DISCOVERY_CACHE_AT, _DISCOVERY_CACHE_ROWS
    now = datetime.now(timezone.utc)
    if (
        _DISCOVERY_CACHE_AT is not None
        and (now - _DISCOVERY_CACHE_AT).total_seconds() < DISCOVERY_CACHE_SECONDS
    ):
        logger.info(
            "MLB_PROP_DISCOVERY_CACHE_HIT %s",
            json.dumps(
                {
                    "age_seconds": round((now - _DISCOVERY_CACHE_AT).total_seconds(), 3),
                    "strict_poly_rows": len(_DISCOVERY_CACHE_ROWS),
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"),
            ),
        )
        return [dict(row) for row in _DISCOVERY_CACHE_ROWS]

    league, player_targets = await asyncio.gather(
        base._fetch_json(
            http,
            f"{base.POLY}/v2/leagues/mlb/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        ),
        _fetch_kalshi_player_targets(http, now),
    )
    nearby = [
        dict(event)
        for event in league.get("events") or []
        if isinstance(event, dict) and _nearby_event(dict(event), now)
    ]
    logger.info(
        "MLB_PROP_NEARBY_EVENTS %s",
        json.dumps(
            {
                "now": now.isoformat(),
                "nearby_count": len(nearby),
                "nearby": [
                    {"slug": e.get("slug"), "title": e.get("title"), "startTime": e.get("startTime")}
                    for e in nearby[:40]
                ],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str,
        ),
    )

    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
    game_groups, player_groups = await asyncio.gather(
        asyncio.gather(
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
        ),
        asyncio.gather(
            *(
                _search_one_player(http, target=target, semaphore=semaphore)
                for target in player_targets.values()
            )
        ),
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in [*game_groups, *player_groups]:
        for row in group:
            slug = str(row.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(row)

    _DISCOVERY_CACHE_AT = datetime.now(timezone.utc)
    _DISCOVERY_CACHE_ROWS = [dict(row) for row in out]
    logger.info(
        "MLB_PROP_SEARCH_DISCOVERY_SUMMARY %s",
        json.dumps(
            {
                "nearby_event_count": len(nearby),
                "kalshi_player_searches": len(player_targets),
                "strict_poly_rows": len(out),
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"),
        ),
    )
    return out


base._fetch_poly_mlb = _fetch_poly_mlb_search


if __name__ == "__main__":
    base.main()
