"""Direct Gemini <-> Kalshi MLB prop prepricing for MVE manufacture ranking.

Uses the same normal-event identity model as the live Gemini/Kalshi MLB prop lane:
exact teams, scheduled start, player, stat family and discrete >=N threshold.  Books
are snapshot-priced through the existing three-venue pricing helpers, so this adds
no persistent websocket consumer.  Prices affect ranking only; Gemini prop lifecycle
rules remain a hard execution blocker for guaranteed-arb classification.
"""
from __future__ import annotations

import asyncio
from datetime import timezone
from decimal import Decimal
import re
from typing import Any, Mapping

import httpx

from src import sports_kp_mve_poly_preprice as ranking
from src.mlb_game_side_normalization import MLB_ALIASES, NormalizedSide, _identity, canonical_team, normalize_gemini_mlb
from src.strict_mlb_prop_adapters import _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import KALSHI_EVENT_TIME_RE, kalshi_event_start_utc
from src.three_venue_mlb_prop_safe_scan import _discrete_threshold_and_side, _price_sides
from src.universal_sports_identity import SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ONE = D("1")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
MAX_START_SKEW_MS = 5 * 60 * 1000

SERIES_TO_FAMILY = {
    "KXMLBHIT": "baseball_player_hits",
    "KXMLBHR": "baseball_player_home_runs",
    "KXMLBTB": "baseball_player_total_bases",
    "KXMLBKS": "baseball_player_strikeouts",
}
FAMILY_TO_GEMINI_STAT = {
    "baseball_player_hits": "hits",
    "baseball_player_home_runs": "home_runs",
    "baseball_player_total_bases": "total_bases",
    "baseball_player_strikeouts": "strikeouts",
}
HR_RE = re.compile(r"^(?P<player>.+?):\s*(?P<threshold>\d+(?:\.\d+)?)\+\s*home runs?\?", re.I)


def _kalshi_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    ticker = str(event.get("event_ticker") or "").upper()
    match = KALSHI_EVENT_TIME_RE.search(ticker)
    if match is None:
        return None
    suffix = match.group("teams").upper()
    raw_codes = sorted(set(MLB_ALIASES), key=lambda value: (-len(value), value))
    matches: set[tuple[str, str]] = set()
    for left in raw_codes:
        if not suffix.startswith(left):
            continue
        right = suffix[len(left):]
        if right not in MLB_ALIASES:
            continue
        a = canonical_team(left); b = canonical_team(right)
        if a and b and a != b:
            matches.add(tuple(sorted((a, b))))
    return next(iter(matches)) if len(matches) == 1 else None


def _hr_identity(market: Mapping[str, Any]) -> tuple[str, Decimal] | None:
    text = str(market.get("title") or market.get("yes_sub_title") or market.get("subtitle") or "").strip()
    m = HR_RE.search(text)
    if m is None:
        return None
    try:
        threshold = D(m.group("threshold"))
    except Exception:
        return None
    return m.group("player").strip(), threshold


async def _fetch_series(http: httpx.AsyncClient, series: str) -> list[dict[str, Any]]:
    for attempt in range(4):
        r = await http.get(f"{KALSHI}/events", params={
            "limit": 200, "status": "open", "series_ticker": series,
            "with_nested_markets": "true",
        })
        if r.status_code != 429:
            r.raise_for_status()
            body = r.json() if r.content else {}
            return [dict(x) for x in (body.get("events") or []) if isinstance(x, Mapping)]
        await asyncio.sleep(0.75 * (2 ** attempt))
    return []


async def _inventory() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    async with httpx.AsyncClient(timeout=25.0) as http:
        gemini_task = asyncio.create_task(fetch_gemini_mlb(http))
        kalshi: list[dict[str, Any]] = []
        for i, series in enumerate(SERIES_TO_FAMILY):
            kalshi.extend(await _fetch_series(http, series))
            if i + 1 < len(SERIES_TO_FAMILY):
                await asyncio.sleep(0.25)
        gemini = await gemini_task
    return gemini, kalshi


def _candidate_sides(
    kalshi_events: list[dict[str, Any]],
    gemini_events: list[dict[str, Any]],
    wanted_tickers: set[str],
) -> tuple[list[NormalizedSide], dict[str, str], int]:
    gemini_props = [row for row in normalize_gemini_mlb(gemini_events) if row.identity.market_family == "player_prop"]
    sides: list[NormalizedSide] = []
    key_by_ticker: dict[str, str] = {}
    exact_joins = 0
    seen: set[tuple[str, str, str]] = set()

    for event in kalshi_events:
        series = str(event.get("series_ticker") or "")
        family = SERIES_TO_FAMILY.get(series)
        stat = FAMILY_TO_GEMINI_STAT.get(family or "")
        teams = _kalshi_teams(event)
        if family is None or stat is None or teams is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker not in wanted_tickers:
                continue
            if family == "baseball_player_home_runs":
                parsed = _hr_identity(market)
            else:
                parsed = _kalshi_identity(event, market, family)
            if parsed is None:
                continue
            player, threshold = parsed
            start = kalshi_event_start_utc(event, market)
            if start is None:
                continue
            event_id = SportsEventIdentity(
                sport="baseball", league="mlb", participants=teams,
                scheduled_start_ms=int(start.timestamp() * 1000),
            )
            subject = _name(player)
            kside = NormalizedSide(
                _identity(venue="kalshi", market_id=ticker, event=event_id,
                          family="player_prop", subject_type="player", subject=subject,
                          stat=stat, comparator="GE", line=threshold),
                ticker, "YES", str(market.get("title") or market.get("yes_sub_title") or ticker),
            )
            matching: list[NormalizedSide] = []
            for grow in gemini_props:
                gid = grow.identity
                if gid.stat != stat or _name(gid.subject) != subject:
                    continue
                if set(event_id.participants) != set(gid.event.participants):
                    continue
                if abs(gid.event.scheduled_start_ms - event_id.scheduled_start_ms) > MAX_START_SKEW_MS:
                    continue
                mapped = _discrete_threshold_and_side(gid.comparator, gid.line)
                if mapped is None:
                    continue
                mapped_threshold, source_side = mapped
                if mapped_threshold != threshold:
                    continue
                matching.append(NormalizedSide(
                    _identity(venue="gemini", market_id=grow.venue_market_id, event=gid.event,
                              family="player_prop", subject_type="player", subject=subject,
                              stat=stat, comparator="GE", line=threshold),
                    grow.venue_market_id, source_side, grow.source_label,
                ))
            # Ambiguous multiple Gemini representations fail closed for ranking too.
            unique = {(g.venue_market_id, g.source_side, g.contract_key): g for g in matching}
            if len(unique) != 1:
                continue
            grow = next(iter(unique.values()))
            key_by_ticker[ticker] = kside.contract_key
            exact_joins += 1
            for side in (kside, grow):
                marker = (side.identity.venue, side.venue_market_id, side.contract_key)
                if marker not in seen:
                    seen.add(marker); sides.append(side)
    return sides, key_by_ticker, exact_joins


async def preprice(event_legs: dict[str, list[Any]]) -> dict[str, Any]:
    legs = [leg for rows in event_legs.values() for leg in rows]
    wanted = {str(leg.market_ticker) for leg in legs if str(leg.market_ticker).upper().startswith(tuple(SERIES_TO_FAMILY))}
    if not wanted:
        return {"wanted": 0, "exact_gk_joins": 0, "priced": 0, "improved": 0, "best_saving": "0"}

    gemini_events, kalshi_events = await _inventory()
    sides, key_by_ticker, exact_joins = _candidate_sides(kalshi_events, gemini_events, wanted)
    async with httpx.AsyncClient(timeout=25.0) as http:
        priced, errors = await _price_sides(http, sides, gemini_events, kalshi_events)

    gemini_by_key: dict[str, list[Any]] = {}
    for row in priced:
        if row.quote.venue == "gemini":
            gemini_by_key.setdefault(row.quote.settlement_key, []).append(row)

    improved = 0
    best_saving = D("0")
    priced_matches = 0
    for leg in legs:
        ticker = str(leg.market_ticker)
        key = key_by_ticker.get(ticker)
        if key is None:
            continue
        candidates = gemini_by_key.get(key) or []
        if len(candidates) != 1:
            continue
        row = candidates[0]; q = row.quote
        if str(leg.hedge_side).lower() == "yes":
            cost, depth = q.yes_cost, q.yes_depth
        elif str(leg.hedge_side).lower() == "no":
            cost, depth = q.no_cost, q.no_depth
        else:
            continue
        priced_matches += 1
        if depth < ONE or cost <= 0 or cost >= leg.hedge_all_in:
            continue
        cache_key = (ticker, str(leg.side).lower())
        previous = ranking._EFFECTIVE_COMPLEMENT_COST.get(cache_key, leg.hedge_all_in)
        if cost >= previous:
            continue
        ranking._EFFECTIVE_COMPLEMENT_COST[cache_key] = cost
        ranking._EFFECTIVE_SOURCE[cache_key] = {
            "venue": "gemini",
            "market_id": q.market_id,
            "settlement_key": q.settlement_key,
            "all_in_q1": str(cost),
            "depth_q1": str(depth),
            "savings_q1": str(leg.hedge_all_in - cost),
            "depth_source": row.depth_source,
            "scope": "MLB_PROP_GEMINI_EXACT_PLAYER_LINE_GAME",
            "rule_gate": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "rules_certified": False,
            "execution_eligible": False,
        }
        improved += 1
        best_saving = max(best_saving, leg.hedge_all_in - cost)

    return {
        "wanted": len(wanted), "kalshi_events": len(kalshi_events), "gemini_events": len(gemini_events),
        "exact_gk_joins": exact_joins, "canonical_sides": len(sides), "priced": priced_matches,
        "pricing_errors": len(errors), "improved": improved, "best_saving": str(best_saving),
        "ranking_only": True, "execution_eligible": False,
    }
