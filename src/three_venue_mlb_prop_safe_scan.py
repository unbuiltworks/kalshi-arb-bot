"""Fast read-only three-venue MLB player-prop scanner.

Reuses the existing strict Polymarket<->Kalshi prop matcher, then safely joins any
Gemini discrete-count prop that represents the same >=N proposition. Kalshi prices
come from nested event snapshots, avoiding one REST order-book request per market.
All output is research-only: live MLB participation can remove the participation
blocker, but complete venue lifecycle certification remains a separate hard gate.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import time
from typing import Any, Mapping

import httpx

from src.mlb_game_side_normalization import NormalizedSide, _identity, canonical_team, normalize_gemini_mlb
from src.mlb_live_participation import candidate_game, fetch_game_boxscores, participation_proof, proof_dict
from src.mlb_prop_rule_certifications import apply_prop_certification
from src.strict_mlb_prop_adapters import _name, _poly_identity
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc, polymarket_start_utc
from src.strict_mlb_prop_snapshot import (
    POLY,
    StrictCandidate,
    _fetch_kalshi_events,
    _fetch_poly_mlb,
    discover_strict_candidates,
    poly_top_book,
)
from src.three_venue_mlb_price_scan import _dedupe, _gemini_contracts, _get
from src.three_venue_mlb_snapshot_pricing import _kalshi_markets, _series_multipliers, kalshi_nested_top_book
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_sports_identity import SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal

# Strict Kalshi/Polymarket families that have a direct Gemini count-stat analogue.
FAMILY_TO_GEMINI_STAT = {
    "baseball_player_hits": "hits",
    "baseball_player_total_bases": "total_bases",
    "baseball_player_strikeouts": "strikeouts",
}


def _canonical_teams(teams: tuple[str, str]) -> tuple[str, str] | None:
    parsed = [canonical_team(team) for team in teams]
    if any(team is None for team in parsed):
        return None
    assert parsed[0] is not None and parsed[1] is not None
    out = tuple(sorted((parsed[0], parsed[1])))
    return out if len(set(out)) == 2 else None


def _discrete_threshold_and_side(comparator: str, line: Decimal | None) -> tuple[Decimal, str] | None:
    """Map a count-stat inequality to the canonical proposition stat >= N.

    ``source_side`` describes whether buying YES on the source Gemini contract buys
    canonical >=N (CONTRACT) or its complement (NO, so orient_book swaps it).
    This works for integer-valued baseball stats without inventing a push outcome.
    """
    if line is None or line < 0:
        return None
    if comparator == "GT":
        threshold = line.to_integral_value(rounding=ROUND_FLOOR) + D("1")
        return threshold, "CONTRACT"
    if comparator == "GE":
        threshold = line.to_integral_value(rounding=ROUND_CEILING)
        return threshold, "CONTRACT"
    if comparator == "LT":
        threshold = line.to_integral_value(rounding=ROUND_CEILING)
        return threshold, "NO"
    if comparator == "LE":
        threshold = line.to_integral_value(rounding=ROUND_FLOOR) + D("1")
        return threshold, "NO"
    return None


def _kalshi_event_map(events: list[dict[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    market_to_event: dict[str, Mapping[str, Any]] = {}
    market_to_series: dict[str, str] = {}
    for event in events:
        series = str(event.get("series_ticker") or "")
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            if ticker:
                market_to_event[ticker] = event
                market_to_series[ticker] = series
    return market_to_event, market_to_series


def _candidate_sides(
    candidates: list[StrictCandidate],
    poly_markets: list[dict[str, Any]],
    kalshi_events: list[dict[str, Any]],
    gemini_events: list[dict[str, Any]],
) -> tuple[
    list[NormalizedSide],
    dict[str, list[str]],
    list[dict[str, Any]],
    dict[str, dict[str, str]],
]:
    poly_by_slug = {str(row.get("slug") or ""): row for row in poly_markets if row.get("slug")}
    k_event_by_ticker, _ = _kalshi_event_map(kalshi_events)
    gemini_props = [
        row for row in normalize_gemini_mlb(gemini_events)
        if row.identity.market_family == "player_prop"
    ]

    sides: list[NormalizedSide] = []
    venues_by_key: dict[str, set[str]] = defaultdict(set)
    join_audit: list[dict[str, Any]] = []
    rule_context: dict[str, dict[str, str]] = {}
    seen_side: set[tuple[str, str, str]] = set()

    for candidate in candidates:
        poly = poly_by_slug.get(candidate.poly_slug)
        event = k_event_by_ticker.get(candidate.kalshi_ticker)
        if poly is None or event is None:
            continue
        p_identity = _poly_identity(poly)
        p_start = polymarket_start_utc(poly)
        if p_identity is None or p_start is None:
            continue
        family, player, raw_teams, _game_date, threshold = p_identity
        if family != candidate.family or threshold != candidate.threshold:
            continue
        teams = _canonical_teams(raw_teams)
        if teams is None:
            continue
        stat = FAMILY_TO_GEMINI_STAT.get(family)
        # Poly/Kalshi remain useful even when this family has no Gemini analogue.
        canonical_stat = stat or family.removeprefix("baseball_player_")
        subject = _name(player)
        p_event = SportsEventIdentity(
            sport="baseball", league="mlb", participants=teams,
            scheduled_start_ms=int(p_start.timestamp() * 1000),
        )
        k_market = next(
            (row for row in event.get("markets") or [] if isinstance(row, Mapping) and str(row.get("ticker") or "") == candidate.kalshi_ticker),
            None,
        )
        k_start = kalshi_event_start_utc(event, k_market or {})
        if k_start is None:
            continue
        k_event = SportsEventIdentity(
            sport="baseball", league="mlb", participants=teams,
            scheduled_start_ms=int(k_start.timestamp() * 1000),
        )
        p_side = NormalizedSide(
            _identity(venue="polymarket", market_id=candidate.poly_slug, event=p_event,
                      family="player_prop", subject_type="player", subject=subject,
                      stat=canonical_stat, comparator="GE", line=threshold),
            candidate.poly_slug, "YES", str(poly.get("question") or poly.get("title") or candidate.poly_slug),
        )
        k_side = NormalizedSide(
            _identity(venue="kalshi", market_id=candidate.kalshi_ticker, event=k_event,
                      family="player_prop", subject_type="player", subject=subject,
                      stat=canonical_stat, comparator="GE", line=threshold),
            candidate.kalshi_ticker, "YES", str((k_market or {}).get("title") or (k_market or {}).get("yes_sub_title") or candidate.kalshi_ticker),
        )
        rule_context[p_side.contract_key] = {
            "family": family,
            "player": str(player),
            "poly_slug": candidate.poly_slug,
        }
        for side in (p_side, k_side):
            marker = (side.identity.venue, side.venue_market_id, side.contract_key)
            if marker not in seen_side:
                seen_side.add(marker)
                sides.append(side)
                venues_by_key[side.contract_key].add(side.identity.venue)

        if stat is None:
            continue
        for grow in gemini_props:
            gid = grow.identity
            if gid.stat != stat or _name(gid.subject) != subject:
                continue
            if {team.lower() for team in teams} != set(gid.event.participants):
                continue
            if abs(gid.event.scheduled_start_ms - p_event.scheduled_start_ms) > 5 * 60 * 1000:
                continue
            mapped = _discrete_threshold_and_side(gid.comparator, gid.line)
            if mapped is None:
                continue
            g_threshold, source_side = mapped
            if g_threshold != threshold:
                continue
            g_side = NormalizedSide(
                _identity(venue="gemini", market_id=grow.venue_market_id, event=gid.event,
                          family="player_prop", subject_type="player", subject=subject,
                          stat=stat, comparator="GE", line=threshold),
                grow.venue_market_id, source_side, grow.source_label,
            )
            marker = (g_side.identity.venue, g_side.venue_market_id, g_side.contract_key)
            if marker in seen_side:
                continue
            seen_side.add(marker)
            sides.append(g_side)
            venues_by_key[g_side.contract_key].add("gemini")
            join_audit.append({
                "settlement_key": g_side.contract_key,
                "gemini_market_id": grow.venue_market_id,
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "player": player,
                "stat": stat,
                "threshold": str(threshold),
                "gemini_source_comparator": gid.comparator,
                "gemini_source_line": str(gid.line),
                "gemini_source_side": source_side,
                "start_skew_ms": abs(gid.event.scheduled_start_ms - p_event.scheduled_start_ms),
            })

    return sides, {key: sorted(value) for key, value in venues_by_key.items()}, join_audit, rule_context


async def _price_sides(
    http: httpx.AsyncClient,
    sides: list[NormalizedSide],
    gemini_events: list[dict[str, Any]],
    kalshi_events: list[dict[str, Any]],
) -> tuple[list[PricedQuote], list[dict[str, str]]]:
    pids = sorted({side.venue_market_id for side in sides if side.identity.venue == "polymarket"})
    sem = asyncio.Semaphore(36)
    p_results = await asyncio.gather(*(_get(http, sem, f"{POLY}/v1/markets/{slug}/book") for slug in pids))
    pbooks: dict[str, tuple[Any, int]] = {}
    errors: list[dict[str, str]] = []
    for slug, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "polymarket", "market_id": slug, "error": error or "book_unparseable"})
        else:
            pbooks[slug] = (book, received)

    gcontracts = _gemini_contracts(gemini_events)
    kmarkets = _kalshi_markets(kalshi_events)
    _k_event_by_ticker, k_series = _kalshi_event_map(kalshi_events)
    series = sorted({k_series.get(side.venue_market_id, "") for side in sides if side.identity.venue == "kalshi" and k_series.get(side.venue_market_id, "")})
    multipliers = await _series_multipliers(http, sem, series, errors)
    snapshot_ms = int(time.time() * 1000)

    priced: list[PricedQuote] = []
    for side in sides:
        row: PricedQuote | None = None
        venue = side.identity.venue
        if venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(
                side, book, received_ms=received,
                depth_source="public_book_top_level", category="sports_mlb_prop",
            )
        elif venue == "kalshi":
            book = kalshi_nested_top_book(kmarkets.get(side.venue_market_id, {}))
            if book is None:
                errors.append({"venue": "kalshi", "market_id": side.venue_market_id, "error": "nested_top_book_unparseable"})
            else:
                row = make_priced_quote(
                    side, book, received_ms=snapshot_ms,
                    kalshi_fee_multiplier=multipliers.get(k_series.get(side.venue_market_id, "")),
                    depth_source="nested_event_market_top_of_book", category="sports_mlb_prop",
                )
        elif venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is None:
                errors.append({"venue": "gemini", "market_id": side.venue_market_id, "error": "buy_yes_no_unparseable"})
            else:
                row = make_priced_quote(
                    side, book, received_ms=snapshot_ms,
                    depth_source="event_prices_q1_probe_only", category="sports_mlb_prop",
                )
        if row is not None:
            priced.append(row)
    return priced, errors


async def run_once() -> dict[str, Any]:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, poly_markets, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http), _fetch_poly_mlb(http), _fetch_kalshi_events(http)
        )
        candidates = discover_strict_candidates(poly_markets, kalshi_events)
        sides, venues_by_key, join_audit, rule_context = _candidate_sides(
            candidates, poly_markets, kalshi_events, gemini_events
        )
        game_refs = [candidate_game(row["poly_slug"]) for row in rule_context.values()]
        dates = sorted({row[0] for row in game_refs if row is not None})
        games = await fetch_game_boxscores(http, dates) if dates else {}
        priced, errors = await _price_sides(http, sides, gemini_events, kalshi_events)

    locks = _dedupe(find_cross_venue_locks(
        [row.quote for row in priced], max_quote_skew_ms=5000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    three_venue_keys = [key for key, venues in venues_by_key.items() if len(venues) == 3]
    positive_keys = {row.settlement_key for row in locks}

    audited_locks: list[Any] = []
    rule_assessments: list[dict[str, Any]] = []
    rule_status_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    participation_proven_keys: set[str] = set()
    certified_locks = 0
    for lock in locks:
        context = rule_context.get(lock.settlement_key)
        if context is None:
            audited_locks.append(lock)
            rule_status_counts["MISSING_RULE_CONTEXT"] += 1
            blocker_counts["missing_rule_context"] += 1
            continue
        proof = participation_proof(
            family=context["family"],
            player_name=context["player"],
            poly_slug=context["poly_slug"],
            games=games,
        )
        upgraded, assessment = apply_prop_certification(
            lock, family=context["family"], proof=proof
        )
        audited_locks.append(upgraded)
        row = assessment.as_dict()
        row.update({
            "settlement_key": lock.settlement_key,
            "player": context["player"],
            "poly_slug": context["poly_slug"],
            "participation_proof": proof_dict(proof),
        })
        rule_assessments.append(row)
        rule_status_counts[assessment.status.value] += 1
        blocker_counts.update(assessment.remaining_blockers)
        if assessment.participation_proven:
            participation_proven_keys.add(lock.settlement_key)
        if assessment.execution_eligible:
            certified_locks += 1

    return {
        "status": "THREE_VENUE_MLB_PROP_SCOPE_SAFE_SCAN",
        "cycle_duration_ms": round((time.monotonic() - started) * 1000, 3),
        "raw": {
            "gemini_events": len(gemini_events),
            "polymarket_prop_markets": len(poly_markets),
            "kalshi_prop_events": len(kalshi_events),
            "mlb_live_or_final_games": len(games),
        },
        "strict_poly_kalshi_candidates": len(candidates),
        "canonical_sides": len(sides),
        "canonical_keys": len(venues_by_key),
        "keys_by_venue_set": dict(Counter("+".join(venues) for venues in venues_by_key.values())),
        "gemini_strict_prop_joins": len(join_audit),
        "three_venue_prop_keys": len(three_venue_keys),
        "gemini_join_audit": join_audit[:30],
        "priced_quotes": len(priced),
        "priced_quotes_by_venue": dict(Counter(row.quote.venue for row in priced)),
        "one_contract_positive_locks": len(locks),
        "certified_actionable_locks": certified_locks,
        "participation_proven_positive_keys": len(participation_proven_keys),
        "top_one_contract_locks": [row.as_dict() for row in audited_locks[:50]],
        "positive_quote_audit": [row.as_dict() for row in priced if row.quote.settlement_key in positive_keys],
        "rule_audit": {
            "status_counts": dict(rule_status_counts),
            "blocker_counts": dict(blocker_counts),
            "top_assessments": rule_assessments[:30],
            "participation_source": "MLB_STATS_API",
            "participation_is_necessary_not_sufficient": True,
            "full_certification_required": True,
        },
        "fetch_error_count": len(errors),
        "fetch_errors": errors[:30],
        "participation_gate": "CONFIRMED_MLB_STATS_API_PARTICIPATION_REQUIRED",
        "rule_gate": "FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_ACTIONABLE",
        "kalshi_price_source": "NESTED_EVENT_MARKET_TOP_OF_BOOK",
        "read_only": True,
        "order_entry": False,
        "execution_eligible": certified_locks > 0,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run_once()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
