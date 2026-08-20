"""One-event FanDuel vs prediction-market MLB player-prop shadow join.

This probe asks The Odds API only for FanDuel player props on the nearest future MLB
game, normalizes offered Over/Under half-point outcomes to exact integer count
propositions, and joins them to the existing strict Polymarket/Kalshi/Gemini prop
identities.  Each FanDuel outcome is one real manual sportsbook leg; the code never
invents a missing opposite sportsbook side.

All prediction costs are the existing conservative q=1 all-in taker costs.  Every
result remains shadow-only because complete cross-venue lifecycle certification is
still required.  There is no order entry and no Pushover delivery in this module.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import os
from typing import Any, Mapping

import httpx

from src.mlb_game_side_normalization import _identity, canonical_team
from src.strict_mlb_prop_adapters import _name
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events, _fetch_poly_mlb, discover_strict_candidates
from src.three_venue_mlb_prop_safe_scan import _candidate_sides, _price_sides
from src.universal_sports_identity import SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ONE = D("1")
MAX_START_SKEW_MS = 5 * 60 * 1000
EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
BOOKMAKER = "fanduel"
REQUEST_MARKETS = (
    "batter_hits_alternate",
    "batter_hits_runs_rbis_alternate",
    "batter_total_bases_alternate",
    "pitcher_strikeouts",
    "pitcher_strikeouts_alternate",
)
MARKET_FAMILY = {
    "batter_hits": ("baseball_player_hits", "hits"),
    "batter_hits_alternate": ("baseball_player_hits", "hits"),
    "batter_hits_runs_rbis": ("baseball_player_hits_runs_rbis", "hits_runs_rbis"),
    "batter_hits_runs_rbis_alternate": ("baseball_player_hits_runs_rbis", "hits_runs_rbis"),
    "batter_total_bases": ("baseball_player_total_bases", "total_bases"),
    "batter_total_bases_alternate": ("baseball_player_total_bases", "total_bases"),
    "pitcher_strikeouts": ("baseball_player_strikeouts", "strikeouts"),
    "pitcher_strikeouts_alternate": ("baseball_player_strikeouts", "strikeouts"),
}


@dataclass(frozen=True)
class FanDuelLeg:
    settlement_key: str
    family: str
    player: str
    stat: str
    threshold: Decimal
    side: str
    decimal_odds: Decimal
    cost_per_dollar: Decimal
    market_key: str
    event_id: str
    event_start_ms: int
    quote_ms: int


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _parse_ms(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def discrete_threshold(point: Decimal) -> Decimal | None:
    """Convert a sportsbook x.5 count line to canonical integer >=N threshold."""
    if point < 0:
        return None
    twice = point * D("2")
    if twice != twice.to_integral_value() or int(twice) % 2 != 1:
        return None
    return point.to_integral_value(rounding=ROUND_FLOOR) + ONE


def outcome_side(name: Any) -> str | None:
    text = str(name or "").strip().lower()
    if text == "over":
        return "YES"
    if text == "under":
        return "NO"
    return None


def _event_identity(event: Mapping[str, Any]) -> SportsEventIdentity | None:
    home = canonical_team(event.get("home_team"))
    away = canonical_team(event.get("away_team"))
    start_ms = _parse_ms(event.get("commence_time"))
    if not home or not away or home == away or start_ms is None:
        return None
    return SportsEventIdentity(
        sport="baseball",
        league="mlb",
        participants=tuple(sorted((home, away))),
        scheduled_start_ms=start_ms,
    )


def normalize_fanduel_event(body: Mapping[str, Any]) -> list[FanDuelLeg]:
    event = _event_identity(body)
    event_id = str(body.get("id") or "")
    if event is None or not event_id:
        return []
    bookmaker = next(
        (
            row for row in body.get("bookmakers") or []
            if isinstance(row, Mapping) and str(row.get("key") or "").lower() == BOOKMAKER
        ),
        None,
    )
    if not isinstance(bookmaker, Mapping):
        return []

    out: list[FanDuelLeg] = []
    seen: set[tuple[str, str, str, Decimal, str]] = set()
    for market in bookmaker.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        market_key = str(market.get("key") or "")
        family_stat = MARKET_FAMILY.get(market_key)
        quote_ms = _parse_ms(market.get("last_update"))
        if family_stat is None or quote_ms is None:
            continue
        family, stat = family_stat
        for row in market.get("outcomes") or []:
            if not isinstance(row, Mapping):
                continue
            side = outcome_side(row.get("name"))
            point = _d(row.get("point"))
            odds = _d(row.get("price"))
            player = str(row.get("description") or "").strip()
            if side is None or point is None or odds is None or odds <= ONE or not player:
                continue
            threshold = discrete_threshold(point)
            if threshold is None:
                continue
            subject = _name(player)
            if not subject:
                continue
            identity = _identity(
                venue="fanduel",
                market_id=f"{event_id}:{market_key}:{subject}:{point}:{side}",
                event=event,
                family="player_prop",
                subject_type="player",
                subject=subject,
                stat=stat,
                comparator="GE",
                line=threshold,
            )
            marker = (identity.contract_key(), market_key, subject, threshold, side)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(FanDuelLeg(
                settlement_key=identity.contract_key(),
                family=family,
                player=player,
                stat=stat,
                threshold=threshold,
                side=side,
                decimal_odds=odds,
                cost_per_dollar=ONE / odds,
                market_key=market_key,
                event_id=event_id,
                event_start_ms=event.scheduled_start_ms,
                quote_ms=quote_ms,
            ))
    return out


def evaluate(
    fd_legs: list[FanDuelLeg],
    prediction_rows: list[Any],
    key_start_ms: Mapping[str, int],
) -> dict[str, Any]:
    by_key: dict[str, list[Any]] = defaultdict(list)
    for row in prediction_rows:
        by_key[row.quote.settlement_key].append(row)

    comparisons = 0
    exact_join_legs = 0
    positives: list[dict[str, Any]] = []
    by_family: Counter[str] = Counter()
    by_pair: Counter[str] = Counter()
    for leg in fd_legs:
        rows = by_key.get(leg.settlement_key, [])
        if not rows:
            continue
        expected_start = key_start_ms.get(leg.settlement_key)
        if expected_start is None or abs(expected_start - leg.event_start_ms) > MAX_START_SKEW_MS:
            continue
        exact_join_legs += 1
        for pred in rows:
            q = pred.quote
            comparisons += 1
            if leg.side == "YES":
                prediction_side = "NO"
                prediction_cost = q.no_cost
                prediction_depth = q.no_depth
            else:
                prediction_side = "YES"
                prediction_cost = q.yes_cost
                prediction_depth = q.yes_depth
            if prediction_depth < ONE:
                continue
            total_cost = leg.cost_per_dollar + prediction_cost
            profit = ONE - total_cost
            if profit <= 0:
                continue
            skew = abs(leg.quote_ms - q.received_ms)
            pair_name = f"fanduel+{q.venue}"
            row = {
                "settlement_key": leg.settlement_key,
                "family": leg.family,
                "player": leg.player,
                "stat": leg.stat,
                "threshold": str(leg.threshold),
                "fanduel_side": leg.side,
                "fanduel_market": leg.market_key,
                "fanduel_decimal_odds": str(leg.decimal_odds),
                "fanduel_cost_per_dollar": str(leg.cost_per_dollar),
                "prediction_venue": q.venue,
                "prediction_market": q.market_id,
                "prediction_side": prediction_side,
                "prediction_all_in_cost": str(prediction_cost),
                "prediction_depth_q1": str(prediction_depth),
                "total_cost_per_dollar": str(total_cost),
                "shadow_profit_per_dollar": str(profit),
                "shadow_roi_on_cost": str(profit / total_cost),
                "quote_skew_ms": skew,
                "fanduel_quote_ms": leg.quote_ms,
                "prediction_quote_ms": q.received_ms,
                "settlement_status": "NOT_EXECUTABLE_PROP_LIFECYCLE_CERTIFICATION_REQUIRED",
                "manual_sportsbook_leg": True,
            }
            positives.append(row)
            by_family[leg.family] += 1
            by_pair[pair_name] += 1
    positives.sort(key=lambda row: D(row["shadow_profit_per_dollar"]), reverse=True)
    return {
        "fanduel_legs": len(fd_legs),
        "fanduel_legs_with_exact_prediction_key": exact_join_legs,
        "directional_comparisons": comparisons,
        "positive_shadow_observations": len(positives),
        "positive_by_family": dict(by_family),
        "positive_by_pair": dict(by_pair),
        "max_shadow_profit_per_dollar": str(max((D(x["shadow_profit_per_dollar"]) for x in positives), default=D("0"))),
        "top_positive": positives[:50],
    }


async def _future_events(http: httpx.AsyncClient, api_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    response = await http.get(EVENTS_URL, params={"apiKey": api_key, "dateFormat": "iso"})
    response.raise_for_status()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = []
    for raw in response.json() if isinstance(response.json(), list) else []:
        if not isinstance(raw, Mapping):
            continue
        start_ms = _parse_ms(raw.get("commence_time"))
        if start_ms is not None and start_ms > now_ms:
            rows.append(dict(raw))
    rows.sort(key=lambda x: _parse_ms(x.get("commence_time")) or 2**63)
    return rows, {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
        "last_cost": response.headers.get("x-requests-last", ""),
    }


async def run() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        future, event_quota = await _future_events(http, api_key)
        if not future:
            return {"status": "FANDUEL_PREDICTION_MLB_PROP_SHADOW", "future_events": 0, "read_only": True, "order_entry": False}
        event = future[0]
        event_id = str(event.get("id") or "")
        fd_response, gemini_events, poly_markets, kalshi_events = await asyncio.gather(
            http.get(
                EVENT_ODDS_URL.format(event_id=event_id),
                params={
                    "apiKey": api_key,
                    "bookmakers": BOOKMAKER,
                    "markets": ",".join(REQUEST_MARKETS),
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
            ),
            fetch_gemini_mlb(http),
            _fetch_poly_mlb(http),
            _fetch_kalshi_events(http),
        )
        fd_response.raise_for_status()
        fd_body = fd_response.json()
        candidates = discover_strict_candidates(poly_markets, kalshi_events)
        sides, venues_by_key, _join_audit, _rule_context = _candidate_sides(
            candidates, poly_markets, kalshi_events, gemini_events
        )
        prediction_rows, prediction_errors = await _price_sides(http, sides, gemini_events, kalshi_events)

    fd_legs = normalize_fanduel_event(fd_body if isinstance(fd_body, Mapping) else {})
    key_start_ms: dict[str, int] = {}
    for side in sides:
        existing = key_start_ms.get(side.contract_key)
        start = side.identity.event.scheduled_start_ms
        if existing is None:
            key_start_ms[side.contract_key] = start
        elif abs(existing - start) > MAX_START_SKEW_MS:
            # Ambiguous same-key game start: remove from exact join eligibility.
            key_start_ms[side.contract_key] = -1
    result = evaluate(fd_legs, prediction_rows, key_start_ms)
    return {
        "status": "FANDUEL_PREDICTION_MLB_PROP_SHADOW",
        "event_id": fd_body.get("id") if isinstance(fd_body, Mapping) else event_id,
        "commence_time": fd_body.get("commence_time") if isinstance(fd_body, Mapping) else event.get("commence_time"),
        "away_team": fd_body.get("away_team") if isinstance(fd_body, Mapping) else event.get("away_team"),
        "home_team": fd_body.get("home_team") if isinstance(fd_body, Mapping) else event.get("home_team"),
        "future_events_seen": len(future),
        "strict_prediction_candidates": len(candidates),
        "prediction_identity_keys": len(venues_by_key),
        "prediction_priced_quotes": len(prediction_rows),
        "prediction_priced_by_venue": dict(Counter(row.quote.venue for row in prediction_rows)),
        "prediction_fetch_errors": prediction_errors[:20],
        "fanduel_market_quota": {
            "remaining": fd_response.headers.get("x-requests-remaining", ""),
            "used": fd_response.headers.get("x-requests-used", ""),
            "last_cost": fd_response.headers.get("x-requests-last", ""),
        },
        "events_endpoint_quota": event_quota,
        **result,
        "rule_gate": "COMPLETE_CROSS_VENUE_PROP_LIFECYCLE_CERTIFICATION_REQUIRED",
        "alert_eligible": False,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "realized_pnl": "0",
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), separators=(",", ":"), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
