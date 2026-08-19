"""Direct read-only Gemini <-> Kalshi MLB player-prop discovery and pricing.

The existing three-venue prop scanner deliberately starts from strict Kalshi <->
Polymarket counterparts.  That is excellent for three-way identity assurance but it
can hide valid Gemini <-> Kalshi normal-event counterparts when Polymarket does not
list the same player/stat/threshold.

This module widens *discovery only*.  It independently joins exact Gemini and Kalshi
player props by:

* MLB game teams;
* scheduled start (<= 5 minutes skew);
* exact player identity;
* exact supported stat family;
* exact discrete >=N proposition.

Pricing and fee logic reuse the existing three-venue scanner.  Complete Gemini rule
fingerprints are not yet audited, so every real pair remains fail-closed for
execution even after MLB participation is proven.  No auth or order-entry code is
present here.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import timezone
from decimal import Decimal
import json
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx

from src.mlb_game_side_normalization import (
    MLB_ALIASES,
    NormalizedSide,
    _identity,
    canonical_team,
    normalize_gemini_mlb,
)
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.mlb_prop_rule_certifications import apply_prop_certification
from src.strict_mlb_prop_adapters import FAMILY_SERIES, _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import KALSHI_EVENT_TIME_RE, kalshi_event_start_utc
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_prop_safe_scan import _discrete_threshold_and_side, _price_sides
from src.three_venue_mlb_price_scan import _dedupe
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_sports_identity import SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
ET = ZoneInfo("America/New_York")
MAX_START_SKEW_MS = 5 * 60 * 1000
SERIES_TO_FAMILY = {series: family for family, series in FAMILY_SERIES.items()}
FAMILY_TO_GEMINI_STAT = {
    "baseball_player_hits": "hits",
    "baseball_player_total_bases": "total_bases",
    "baseball_player_strikeouts": "strikeouts",
}


def _kalshi_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    """Decode the concatenated team suffix in audited MLB prop event tickers.

    Require exactly one canonical two-team split.  Ambiguity fails closed.
    """
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
        a = canonical_team(left)
        b = canonical_team(right)
        if not a or not b or a == b:
            continue
        matches.add(tuple(sorted((a, b))))
    return next(iter(matches)) if len(matches) == 1 else None


def _synthetic_game_slug(teams: tuple[str, str], start_ms: int) -> str:
    dt = __import__("datetime").datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone(ET)
    return f"mlb-{teams[0].lower()}-{teams[1].lower()}-{dt.date().isoformat()}"


def _candidate_sides(
    kalshi_events: list[dict[str, Any]],
    gemini_events: list[dict[str, Any]],
) -> tuple[list[NormalizedSide], dict[str, list[str]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    gemini_props = [
        row for row in normalize_gemini_mlb(gemini_events)
        if row.identity.market_family == "player_prop"
    ]
    sides: list[NormalizedSide] = []
    venues_by_key: dict[str, set[str]] = defaultdict(set)
    joins: list[dict[str, Any]] = []
    rule_context: dict[str, dict[str, str]] = {}
    seen: set[tuple[str, str, str]] = set()

    for event in kalshi_events:
        series = str(event.get("series_ticker") or "")
        family = SERIES_TO_FAMILY.get(series)
        stat = FAMILY_TO_GEMINI_STAT.get(family or "")
        if family is None or stat is None:
            # HRR currently has no direct Gemini analogue in the audited map.
            continue
        teams = _kalshi_teams(event)
        if teams is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            parsed = _kalshi_identity(event, market, family)
            if parsed is None:
                continue
            player, threshold = parsed
            ticker = str(market.get("ticker") or "")
            start = kalshi_event_start_utc(event, market)
            if not ticker or start is None:
                continue
            event_id = SportsEventIdentity(
                sport="baseball",
                league="mlb",
                participants=teams,
                scheduled_start_ms=int(start.timestamp() * 1000),
            )
            subject = _name(player)
            kside = NormalizedSide(
                _identity(
                    venue="kalshi", market_id=ticker, event=event_id,
                    family="player_prop", subject_type="player", subject=subject,
                    stat=stat, comparator="GE", line=threshold,
                ),
                ticker,
                "YES",
                str(market.get("title") or market.get("yes_sub_title") or ticker),
            )

            matching_gemini: list[NormalizedSide] = []
            for grow in gemini_props:
                gid = grow.identity
                if gid.stat != stat or _name(gid.subject) != subject:
                    continue
                if set(event_id.participants) != set(gid.event.participants):
                    continue
                start_skew = abs(gid.event.scheduled_start_ms - event_id.scheduled_start_ms)
                if start_skew > MAX_START_SKEW_MS:
                    continue
                mapped = _discrete_threshold_and_side(gid.comparator, gid.line)
                if mapped is None:
                    continue
                mapped_threshold, source_side = mapped
                if mapped_threshold != threshold:
                    continue
                gside = NormalizedSide(
                    _identity(
                        venue="gemini", market_id=grow.venue_market_id, event=gid.event,
                        family="player_prop", subject_type="player", subject=subject,
                        stat=stat, comparator="GE", line=threshold,
                    ),
                    grow.venue_market_id,
                    source_side,
                    grow.source_label,
                )
                matching_gemini.append(gside)
                joins.append({
                    "settlement_key": gside.contract_key,
                    "family": family,
                    "player": player,
                    "stat": stat,
                    "threshold": str(threshold),
                    "kalshi_ticker": ticker,
                    "gemini_market_id": grow.venue_market_id,
                    "gemini_source_comparator": gid.comparator,
                    "gemini_source_line": str(gid.line),
                    "gemini_source_side": source_side,
                    "teams": list(event_id.participants),
                    "start_skew_ms": start_skew,
                })

            if not matching_gemini:
                continue
            context = {
                "family": family,
                "player": player,
                "game_slug": _synthetic_game_slug(teams, event_id.scheduled_start_ms),
            }
            rule_context[kside.contract_key] = context
            for side in (kside, *matching_gemini):
                marker = (side.identity.venue, side.venue_market_id, side.contract_key)
                if marker in seen:
                    continue
                seen.add(marker)
                sides.append(side)
                venues_by_key[side.contract_key].add(side.identity.venue)

    return sides, {key: sorted(value) for key, value in venues_by_key.items()}, joins, rule_context


async def run_once() -> dict[str, Any]:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http), _fetch_kalshi_events(http)
        )
        sides, venues_by_key, joins, rule_context = _candidate_sides(kalshi_events, gemini_events)
        dates = sorted({context["game_slug"].split("-")[-3] + "-" + context["game_slug"].split("-")[-2] + "-" + context["game_slug"].split("-")[-1] for context in rule_context.values()})
        games = await fetch_game_boxscores(http, dates) if dates else {}
        priced, errors = await _price_sides(http, sides, gemini_events, kalshi_events)

    locks = _dedupe(find_cross_venue_locks(
        [row.quote for row in priced],
        max_quote_skew_ms=5000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    assessments: list[dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    participation_proven = 0
    certified = 0
    for lock in locks:
        context = rule_context.get(lock.settlement_key)
        if context is None:
            blockers["missing_rule_context"] += 1
            continue
        proof = participation_proof(
            family=context["family"],
            player_name=context["player"],
            poly_slug=context["game_slug"],
            games=games,
        )
        _upgraded, assessment = apply_prop_certification(
            lock, family=context["family"], proof=proof
        )
        row = assessment.as_dict()
        row.update({
            "settlement_key": lock.settlement_key,
            "player": context["player"],
            "participation_proof": proof_dict(proof),
        })
        assessments.append(row)
        blockers.update(assessment.remaining_blockers)
        participation_proven += int(assessment.participation_proven)
        certified += int(assessment.execution_eligible)

    return {
        "status": "GEMINI_KALSHI_MLB_PROP_SHADOW",
        "gemini_events": len(gemini_events),
        "kalshi_prop_events": len(kalshi_events),
        "exact_gk_joins": len(joins),
        "exact_gk_keys": len(venues_by_key),
        "join_audit": joins[:25],
        "priced_quotes": len(priced),
        "priced_quotes_by_venue": dict(Counter(row.quote.venue for row in priced)),
        "fetch_errors": errors[:20],
        "one_contract_positive_locks": len(locks),
        "top_positive_locks": [
            {
                "settlement_key": row.settlement_key,
                "yes_venue": row.yes_venue,
                "no_venue": row.no_venue,
                "unit_cost": str(row.unit_cost),
                "unit_profit": str(row.unit_profit),
                "max_quantity": str(row.max_quantity),
                "quote_skew_ms": row.quote_skew_ms,
            }
            for row in locks[:20]
        ],
        "participation_proven_positive_locks": participation_proven,
        "certified_actionable_locks": certified,
        "rule_blockers": dict(blockers),
        "rule_assessments": assessments[:20],
        "rule_gate": "GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "cycle_ms": round((time.monotonic() - started) * 1000, 3),
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }


async def run_forever(interval_seconds: float = 10.0) -> None:
    cycle = 0
    while True:
        cycle += 1
        try:
            result = await run_once()
            print(json.dumps({"cycle": cycle, **result}, separators=(",", ":"), default=str), flush=True)
        except Exception as exc:
            print(json.dumps({
                "cycle": cycle,
                "status": "GEMINI_KALSHI_MLB_PROP_SHADOW_ERROR",
                "error": type(exc).__name__,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        await asyncio.sleep(max(1.0, interval_seconds))


if __name__ == "__main__":
    asyncio.run(run_forever())
