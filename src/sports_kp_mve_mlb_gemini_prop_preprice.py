"""Direct Gemini <-> Kalshi MLB prop prepricing for MVE manufacture ranking.

The current MVE pass has already fetched the relevant Kalshi prop markets.  Reuse
that in-memory cache and fetch only Gemini MLB inventory here, eliminating duplicate
Kalshi series reads from the hot path.  Matching remains exact on game teams/start,
player, stat family and discrete >=N threshold.  Pricing uses Gemini executable
best bid/ask fields with conservative q=1 fees.

This is ranking-only. Gemini prop lifecycle rules remain explicitly uncertified for
guaranteed-arbitrage execution.
"""
from __future__ import annotations

from decimal import Decimal
import re
import time
from typing import Any, Mapping

import httpx

from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_mve_poly_preprice as ranking
from src.mlb_game_side_normalization import MLB_ALIASES, NormalizedSide, _identity, canonical_team, normalize_gemini_mlb
from src.strict_mlb_prop_adapters import FAMILY_LABELS, _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import KALSHI_EVENT_TIME_RE, kalshi_event_start_utc
from src.three_venue_mlb_price_scan import _gemini_contracts
from src.three_venue_mlb_prop_safe_scan import _discrete_threshold_and_side
from src.three_venue_pricing_core import gemini_contract_book, make_priced_quote
from src.universal_sports_identity import SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ONE = D("1")
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


def _series_from_ticker(ticker: str) -> str | None:
    upper = str(ticker or "").upper()
    for series in SERIES_TO_FAMILY:
        if upper.startswith(series + "-"):
            return series
    return None


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


def _cached_candidate_sides(
    event_legs: dict[str, list[Any]],
    gemini_events: list[dict[str, Any]],
) -> tuple[list[NormalizedSide], dict[str, NormalizedSide], int, int]:
    gemini_props = [row for row in normalize_gemini_mlb(gemini_events) if row.identity.market_family == "player_prop"]
    matched_by_ticker: dict[str, NormalizedSide] = {}
    exact_joins = 0
    missing_cached = 0
    sides: list[NormalizedSide] = []
    seen: set[tuple[str, str, str]] = set()

    for rows in event_legs.values():
        for leg in rows:
            ticker = str(leg.market_ticker)
            series = _series_from_ticker(ticker)
            family = SERIES_TO_FAMILY.get(series or "")
            stat = FAMILY_TO_GEMINI_STAT.get(family or "")
            if series is None or family is None or stat is None:
                continue
            market = v8._MARKETS.get(ticker)
            if not isinstance(market, Mapping):
                missing_cached += 1
                continue
            event_ticker = str(leg.event_ticker)
            # Series and market text provide the two independent family signals
            # the strict parser expects; the scheduled game comes from the ticker.
            event = {
                "series_ticker": series,
                "event_ticker": event_ticker,
                "title": (FAMILY_LABELS.get(family) or (family,))[0],
            }
            teams = _kalshi_teams(event)
            if teams is None:
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
            unique = {(g.venue_market_id, g.side, g.contract_key): g for g in matching}
            if len(unique) != 1:
                continue
            grow = next(iter(unique.values()))
            matched_by_ticker[ticker] = grow
            exact_joins += 1
            for side in (kside, grow):
                marker = (side.identity.venue, side.venue_market_id, side.contract_key)
                if marker not in seen:
                    seen.add(marker); sides.append(side)
    return sides, matched_by_ticker, exact_joins, missing_cached


async def preprice(event_legs: dict[str, list[Any]]) -> dict[str, Any]:
    legs = [leg for rows in event_legs.values() for leg in rows]
    wanted = [leg for leg in legs if _series_from_ticker(str(leg.market_ticker)) is not None]
    if not wanted:
        return {"wanted": 0, "exact_gk_joins": 0, "priced": 0, "improved": 0, "best_saving": "0", "kalshi_refetches": 0}

    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini_events = await fetch_gemini_mlb(http)
    _sides, matched_by_ticker, exact_joins, missing_cached = _cached_candidate_sides(event_legs, gemini_events)
    contracts = _gemini_contracts(gemini_events)

    priced_by_ticker: dict[str, Any] = {}
    pricing_errors = 0
    now_ms = int(time.time() * 1000)
    for ticker, grow in matched_by_ticker.items():
        book = gemini_contract_book(contracts.get(grow.venue_market_id, {}))
        if book is None:
            pricing_errors += 1
            continue
        row = make_priced_quote(
            grow, book, received_ms=now_ms,
            depth_source="event_prices_q1_probe_only", category="sports_mlb_prop",
        )
        if row is None:
            pricing_errors += 1
            continue
        priced_by_ticker[ticker] = row

    improved = 0
    best_saving = D("0")
    priced_matches = 0
    for leg in wanted:
        ticker = str(leg.market_ticker)
        row = priced_by_ticker.get(ticker)
        if row is None:
            continue
        q = row.quote
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
        "wanted": len(wanted), "gemini_events": len(gemini_events),
        "exact_gk_joins": exact_joins, "priced": priced_matches,
        "pricing_errors": pricing_errors, "improved": improved, "best_saving": str(best_saving),
        "missing_cached_markets": missing_cached, "kalshi_refetches": 0,
        "ranking_only": True, "execution_eligible": False,
    }
