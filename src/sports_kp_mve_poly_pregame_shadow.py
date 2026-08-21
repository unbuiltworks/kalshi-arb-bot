"""Read-only pregame Polymarket replacement-cost shadow for generated NFL MVEs.

Fetches active/open Polymarket US sports events through the MVE horizon, strictly
matches NFL moneyline and spread propositions to Kalshi component identities,
and reads actual top-of-book q=1 depth.  Matching is normal-event economics only:
settlement-rule equivalence is intentionally not asserted and no order entry is
exposed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from typing import Any, Mapping

import httpx

from src.gemini_sports_adapter import canonical_team
from src.polymarket_kalshi_shadow import polymarket_taker_fee
from src.polymarket_kalshi_shadow_v2 import PolyBookStore, _fetch_poly_book
from src.sports_kp_mve_cross_venue_shadow import _d, _event_bits, _kalshi_component, _kalshi_identity_key

D = Decimal
ONE = D("1")
POLY_GATEWAY = "https://gateway.polymarket.us"
POLY_FEE_RATE = D("0.05")  # existing repo-wide K<->P shadow convention
PAGE = 100
MAX_PAGES = 12
NFL_TEAMS = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAC","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WSH",
}


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _poly_type(market: Mapping[str, Any]) -> str:
    raw = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or market.get("marketType") or "")
    text = raw.upper().replace("SPORTS_MARKET_TYPE_", "")
    if "MONEYLINE" in text or text in {"MONEY_LINE", "MONEYLINE"}:
        return "moneyline"
    if "SPREAD" in text:
        return "spread"
    return ""


def _team_code(value: Any) -> str | None:
    raw = canonical_team(value).upper()
    return raw if raw in NFL_TEAMS else None


def _event_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    found: list[str] = []
    for participant in event.get("participants") or []:
        if not isinstance(participant, Mapping):
            continue
        team = participant.get("team") if isinstance(participant.get("team"), Mapping) else {}
        for value in (team.get("abbreviation"), team.get("displayAbbreviation"), team.get("name"), participant.get("name")):
            code = _team_code(value)
            if code and code not in found:
                found.append(code); break
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        for side in market.get("marketSides") or []:
            if not isinstance(side, Mapping):
                continue
            team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
            for value in (team.get("abbreviation"), team.get("displayAbbreviation"), team.get("name"), side.get("description"), side.get("identifier")):
                code = _team_code(value)
                if code and code not in found:
                    found.append(code); break
        if len(found) >= 2:
            break
    unique = sorted(set(found))
    return tuple(unique) if len(unique) == 2 else None


def _long_team(market: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    hits: list[str] = []
    for side in market.get("marketSides") or []:
        if not isinstance(side, Mapping) or not bool(side.get("long")):
            continue
        team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
        for value in (team.get("abbreviation"), team.get("displayAbbreviation"), team.get("name"), side.get("description"), side.get("identifier")):
            code = _team_code(value)
            if code in teams:
                hits.append(code); break
    unique = sorted(set(hits))
    return unique[0] if len(unique) == 1 else None


def _event_start(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    for value in (market.get("gameStartTime"), market.get("startTime"), event.get("startTime"), event.get("startDate"), event.get("eventDate")):
        dt = _parse_dt(value)
        if dt is not None:
            return dt
    return None


def _poly_identity(event: Mapping[str, Any], market: Mapping[str, Any]) -> tuple[tuple[Any, ...], str] | None:
    typ = _poly_type(market)
    if typ not in {"moneyline", "spread"}:
        return None
    slug = str(market.get("slug") or market.get("id") or "")
    if not slug or market.get("active") is False or market.get("closed") is True or market.get("acceptingOrders") is False:
        return None
    teams = _event_teams(event)
    start = _event_start(event, market)
    if teams is None or start is None:
        return None
    long_team = _long_team(market, teams)
    if long_team is None:
        return None
    day = start.date().isoformat()
    if typ == "moneyline":
        return ((day, teams, typ, long_team.lower(), "", "YES", None), slug)
    line = _d(market.get("line"))
    if line is None:
        return None
    # Polymarket's team spread line is the displayed handicap.  The canonical
    # proposition is team margin > -handicap; only positive half-point margin
    # thresholds are comparable to the Kalshi KXNFLSPREAD family used here.
    threshold = -line
    if threshold <= 0 or threshold * 2 != (threshold * 2).to_integral_value() or int(threshold * 2) % 2 != 1:
        return None
    return ((day, teams, typ, long_team.lower(), "points", "GT", threshold), slug)


def _key_compatible(kkey: tuple[Any, ...], pkey: tuple[Any, ...]) -> bool:
    # Exact proposition dimensions except allow a one-day UTC/local date skew;
    # team pair + family + subject + line remain exact and each Kalshi component
    # must resolve to a unique Polymarket slug below.
    if kkey[1:] != pkey[1:]:
        return False
    try:
        kd = datetime.fromisoformat(str(kkey[0])).date()
        pd = datetime.fromisoformat(str(pkey[0])).date()
    except Exception:
        return False
    return abs((kd - pd).days) <= 1


async def _pregame_events(horizon_hours: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max(1, min(horizon_hours, 48)))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=15.0) as http:
        for page in range(MAX_PAGES):
            r = await http.get(
                f"{POLY_GATEWAY}/v1/events",
                params={
                    "limit": PAGE, "offset": page * PAGE, "active": "true",
                    "closed": "false", "ended": "false", "categories": "sports",
                    "startTimeMax": horizon.isoformat().replace("+00:00", "Z"),
                    "orderBy": "startTime", "orderDirection": "desc",
                },
            )
            r.raise_for_status()
            body = r.json() if r.content else {}
            rows = body.get("events") or [] if isinstance(body, dict) else []
            for raw in rows:
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                key = str(row.get("id") or row.get("gameId") or row.get("slug") or "")
                start = _event_start(row, {})
                if not key or key in seen or start is None:
                    continue
                # Keep events from shortly before now too; delayed starts can
                # remain pregame/open while nominal startTime has passed.
                if start < now - timedelta(hours=3) or start > horizon:
                    continue
                seen.add(key); out.append(row)
            if len(rows) < PAGE:
                break
    return out


async def annotate_poly_pregame(
    result: dict[str, Any], market_cache: dict[str, dict[str, Any]], *, horizon_hours: int = 36
) -> dict[str, Any]:
    rows = [x for x in (result.get("ranked") or []) if isinstance(x, dict)]
    if not rows:
        result["poly_pregame_shadow_candidates"] = 0
        result["poly_pregame_shadow_best"] = None
        return result

    kalshi_keys: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        for leg in row.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            mt = str(leg.get("market_ticker") or "")
            key = _kalshi_identity_key(str(leg.get("event_ticker") or ""), mt)
            if mt and key is not None:
                kalshi_keys[mt] = key
    if not kalshi_keys:
        result["poly_pregame_shadow_candidates"] = 0
        result["poly_pregame_shadow_best"] = None
        return result

    events = await _pregame_events(horizon_hours)
    poly_rows: list[tuple[tuple[Any, ...], str]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            parsed = _poly_identity(event, market)
            if parsed is not None:
                poly_rows.append(parsed)

    matches: dict[str, str] = {}
    for mt, kkey in kalshi_keys.items():
        slugs = sorted({slug for pkey, slug in poly_rows if _key_compatible(kkey, pkey)})
        if len(slugs) == 1:
            matches[mt] = slugs[0]

    store = PolyBookStore()
    slugs = sorted(set(matches.values()))
    if slugs:
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=8.0) as http:
            await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])

    positives = 0
    best: dict[str, Any] | None = None
    for row in rows:
        base_all_in = _d(row.get("all_in_cost_conservative"))
        if base_all_in is None:
            continue
        # Start from any Gemini savings v10 already measured, then allow Poly to
        # beat the remaining Kalshi/Gemini component cost.  Each leg independently
        # chooses the cheapest observed shadow venue without changing live gates.
        gem = row.get("cross_venue_shadow") if isinstance(row.get("cross_venue_shadow"), dict) else {}
        gem_by_mt = {str(x.get("kalshi_market_ticker")): x for x in gem.get("replacements") or [] if isinstance(x, dict)}
        total_savings = D("0")
        replacements: list[dict[str, Any]] = []
        for leg in row.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            mt = str(leg.get("market_ticker") or "")
            market = market_cache.get(mt) or {}
            k = _kalshi_component(market, str(leg.get("side") or ""), inverse=False)
            if k is None:
                continue
            side, kp, kf, kd = k
            baseline_cost = kp + kf
            chosen_venue = "kalshi"
            chosen_cost = baseline_cost
            chosen_depth = kd
            chosen_market = mt

            grow = gem_by_mt.get(mt)
            if grow:
                gcost = _d(grow.get("gemini_all_in_q1")); gdepth = _d(grow.get("gemini_depth_q1"))
                if gcost is not None and gdepth is not None and gdepth >= ONE and gcost < chosen_cost:
                    chosen_venue = "gemini"; chosen_cost = gcost; chosen_depth = gdepth
                    chosen_market = str(grow.get("gemini_symbol") or "")

            slug = matches.get(mt)
            pq = store.quote(slug, max_age_ms=15000) if slug else None
            if pq is not None:
                if side == "yes": pprice, pdepth = pq.yes_ask, pq.yes_depth
                else: pprice, pdepth = pq.no_ask, pq.no_depth
                if pdepth >= ONE and D("0") < pprice < ONE:
                    pcost = pprice + polymarket_taker_fee(1, pprice, POLY_FEE_RATE)
                    if pcost < chosen_cost:
                        chosen_venue = "polymarket"; chosen_cost = pcost; chosen_depth = pdepth; chosen_market = slug or ""

            savings = max(D("0"), baseline_cost - chosen_cost)
            total_savings += savings
            replacements.append({
                "kalshi_market_ticker": mt,
                "hedge_side": side,
                "baseline_kalshi_all_in_q1": str(baseline_cost),
                "cheapest_venue": chosen_venue,
                "cheapest_market_id": chosen_market,
                "cheapest_all_in_q1": str(chosen_cost),
                "savings_q1": str(savings),
                "cheapest_depth_q1": str(chosen_depth),
                "polymarket_slug": slug,
                "rules_certified": chosen_venue == "kalshi",
                "execution_eligible": chosen_venue == "kalshi",
            })

        all_in = base_all_in - total_savings
        locked = ONE - all_in
        shadow = {
            "direction": "COMBO_YES_PLUS_CHEAPEST_KALSHI_GEMINI_POLY_COMPONENTS",
            "total_component_savings_q1": str(total_savings),
            "all_in_cost_shadow": str(all_in),
            "locked_profit_shadow": str(locked),
            "classification": "POSITIVE_ECONOMICS_RULES_UNCERTIFIED" if locked > 0 else "NO_TRADE",
            "execution_eligible": False,
            "reason": "Cross-venue replacements require exact settlement-rule certification before execution",
            "replacements": replacements,
        }
        row["poly_pregame_shadow"] = shadow
        if locked > 0:
            positives += 1
        cand = {"ticker": row.get("ticker"), "legs": row.get("legs"), **shadow}
        if best is None or D(cand["locked_profit_shadow"]) > D(best["locked_profit_shadow"]):
            best = cand

    result["poly_pregame_shadow_candidates"] = positives
    result["poly_pregame_shadow_best"] = best
    result["poly_pregame_shadow_events_scanned"] = len(events)
    result["poly_pregame_shadow_matched_kalshi_components"] = len(matches)
    result["poly_pregame_shadow_books"] = store.quote_count
    result["poly_pregame_shadow_execution_enabled"] = False
    return result
