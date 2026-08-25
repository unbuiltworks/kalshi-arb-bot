"""One-shot read-only MLB threshold scanner for Kalshi/Gemini/Polymarket/FanDuel.

The existing exact-counterpart scanner starts from identical settlement keys.
This scanner deliberately prices *all* supported full-game threshold rows first,
then compares different thresholds inside the same normalized market family.

Physical game identity is fail-closed. A normalized settlement key is not enough:
feeds can expose the same teams/market family for multiple dates, and a stale or
mis-normalized scheduled start can otherwise create false cross-game locks. Every
observed quote therefore carries an explicit MLB Eastern game date. If a venue
market id contains YYYY-MM-DD, that embedded date is authoritative for matching;
otherwise the date is derived from the scheduled start in America/New_York.

FanDuel prices come from The Odds API discovery feed. Sportsbook legs are
manual-only; this process has no authenticated order clients and never places an
order. All candidate P/L is q=1 shadow math, never realized P/L.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import re
import time
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import httpx

from src.four_venue_threshold_middle import (
    FUNDED_VENUES,
    SUPPORTED_COMPARATORS,
    THRESHOLD_FAMILIES,
    ThresholdObservedQuote,
    find_threshold_middles,
)
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import NormalizedSide, normalize_gemini_mlb, normalize_polymarket_mlb
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import KALSHI, POLY, kalshi_top_book, poly_top_book
from src.the_odds_api_mlb_shadow import fetch_sportsbooks, sportsbook_quotes
from src.three_venue_mlb_price_scan import _dedupe, _gemini_contracts, _get, _kalshi_series
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import MarketQuote, find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

D = Decimal
logger = logging.getLogger("kalshi_arb.four_venue_threshold_middle_shadow")
ET = ZoneInfo("America/New_York")
ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _physical_event_date(market_id: str, scheduled_start_ms: int) -> str:
    """Return explicit MLB game date, preferring a date encoded by the venue id."""
    match = ISO_DATE_RE.search(str(market_id or ""))
    if match:
        return match.group(1)
    try:
        if int(scheduled_start_ms) <= 0:
            return ""
        dt = datetime.fromtimestamp(int(scheduled_start_ms) / 1000, tz=timezone.utc).astimezone(ET)
        return dt.date().isoformat()
    except Exception:
        return ""


def _threshold_side(side: NormalizedSide) -> bool:
    identity = side.identity
    return (
        identity.scope == "full_contest"
        and identity.market_family in THRESHOLD_FAMILIES
        and identity.comparator in SUPPORTED_COMPARATORS
        and identity.line is not None
    )


def _unique_sides(rows: Iterable[NormalizedSide]) -> list[NormalizedSide]:
    out: list[NormalizedSide] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (row.identity.venue, row.venue_market_id, row.side, row.contract_key)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


async def price_all_threshold_sides(
    http: httpx.AsyncClient,
    gemini_events: list[dict[str, Any]],
    poly_events: list[dict[str, Any]],
    kalshi_events: list[dict[str, Any]],
) -> tuple[list[tuple[PricedQuote, NormalizedSide]], list[dict[str, str]], int]:
    """Price every supported direct threshold row, not only exact counterparts."""
    normalized = (
        normalize_gemini_mlb(gemini_events)
        + normalize_polymarket_mlb(poly_events)
        + normalize_kalshi_mlb_eastern(kalshi_events)
    )
    wanted = _unique_sides(row for row in normalized if _threshold_side(row))
    gcontracts = _gemini_contracts(gemini_events)
    ktoseries = _kalshi_series(kalshi_events)
    pids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "polymarket"})
    kids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "kalshi"})
    series = sorted({ktoseries.get(t, "") for t in kids if ktoseries.get(t, "")})
    sem = asyncio.Semaphore(20)
    p_results, k_results, s_results = await asyncio.gather(
        asyncio.gather(*(_get(http, sem, f"{POLY}/v1/markets/{x}/book") for x in pids)),
        asyncio.gather(*(_get(http, sem, f"{KALSHI}/markets/{x}/orderbook", params={"depth": 1}) for x in kids)),
        asyncio.gather(*(_get(http, sem, f"{KALSHI}/series/{x}") for x in series)),
    )

    errors: list[dict[str, str]] = []
    pbooks: dict[str, tuple[Any, int]] = {}
    for market_id, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "polymarket", "market_id": market_id, "error": error or "book_unparseable"})
        else:
            pbooks[market_id] = (book, received)

    kbooks: dict[str, tuple[Any, int]] = {}
    for market_id, (body, received, error) in zip(kids, k_results):
        book = kalshi_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "kalshi", "market_id": market_id, "error": error or "book_unparseable"})
        else:
            kbooks[market_id] = (book, received)

    multipliers: dict[str, Decimal] = {}
    for series_ticker, (body, _received, error) in zip(series, s_results):
        meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = _d(meta.get("fee_multiplier"))
        if fee_type in {"quadratic", "quadratic_with_maker_fees"} and multiplier is not None and D("0") <= multiplier <= D("100"):
            multipliers[series_ticker] = multiplier
        else:
            errors.append({"venue": "kalshi", "market_id": series_ticker, "error": error or f"bad_fee_metadata:{fee_type}:{multiplier}"})

    priced: list[tuple[PricedQuote, NormalizedSide]] = []
    now = int(time.time() * 1000)
    for side in wanted:
        row: PricedQuote | None = None
        if side.identity.venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None:
                row = make_priced_quote(side, book, received_ms=now, depth_source="event_prices_q1_probe_only")
            else:
                errors.append({"venue": "gemini", "market_id": side.venue_market_id, "error": "buy_yes_no_unparseable"})
        elif side.identity.venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, depth_source="public_book_top_level")
        elif side.identity.venue == "kalshi" and side.venue_market_id in kbooks:
            book, received = kbooks[side.venue_market_id]
            row = make_priced_quote(
                side,
                book,
                received_ms=received,
                kalshi_fee_multiplier=multipliers.get(ktoseries.get(side.venue_market_id, "")),
                depth_source="public_book_top_level",
            )
        if row is not None:
            priced.append((row, side))
    return priced, errors, len(wanted)


def _direct_observed(priced: PricedQuote, side: NormalizedSide) -> ThresholdObservedQuote:
    identity = side.identity
    event_date = _physical_event_date(side.venue_market_id, identity.event.scheduled_start_ms)
    return ThresholdObservedQuote(
        venue=identity.venue,
        market_id=priced.quote.market_id,
        settlement_key=side.contract_key,
        market_family=identity.market_family,
        scope=identity.scope,
        subject_type=identity.subject_type,
        subject=identity.subject,
        stat=identity.stat,
        comparator=identity.comparator,
        line=identity.line if identity.line is not None else D("0"),
        yes_cost=priced.quote.yes_cost,
        no_cost=priced.quote.no_cost,
        yes_depth=priced.quote.yes_depth,
        no_depth=priced.quote.no_depth,
        scheduled_start_ms=identity.event.scheduled_start_ms,
        received_ms=priced.quote.received_ms,
        physical_event_date=event_date,
        source_side=priced.source_side,
        rules_complete=identity.fully_rule_audited,
        rule_fingerprint=identity.execution_rule_fingerprint,
    )


def _parts(settlement_key: str) -> tuple[str, ...] | None:
    parts = tuple(str(settlement_key).split("|"))
    return parts if len(parts) >= 10 else None


def _fanduel_observed(
    quote: MarketQuote,
    *,
    start_by_event_id: Mapping[str, int],
) -> ThresholdObservedQuote | None:
    if quote.venue != "sportsbook:fanduel":
        return None
    parts = _parts(quote.settlement_key)
    if parts is None:
        return None
    family, scope, subject_type, subject, stat, comparator = parts[3:9]
    line = _d(parts[9])
    if family not in THRESHOLD_FAMILIES or comparator not in SUPPORTED_COMPARATORS or line is None:
        return None
    event_id = quote.market_id.split(":", 1)[0]
    start = start_by_event_id.get(event_id)
    if start is None:
        return None
    event_date = _physical_event_date(quote.market_id, start)
    return ThresholdObservedQuote(
        venue=quote.venue,
        market_id=quote.market_id,
        settlement_key=quote.settlement_key,
        market_family=family,
        scope=scope,
        subject_type=subject_type,
        subject=subject,
        stat=stat,
        comparator=comparator,
        line=line,
        yes_cost=quote.yes_cost,
        no_cost=quote.no_cost,
        yes_depth=quote.yes_depth,
        no_depth=quote.no_depth,
        scheduled_start_ms=start,
        received_ms=quote.received_ms,
        physical_event_date=event_date,
        source_side="CONTRACT",
        rules_complete=False,
        rule_fingerprint="INCOMPLETE",
    )


def _sportsbook_start_map(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for event in events:
        event_id = str(event.get("id") or "")
        raw = event.get("commence_time")
        if not event_id or not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
            out[event_id] = int(dt.timestamp() * 1000)
        except Exception:
            continue
    return out


def _venue_pair_counts(candidates: Iterable[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        counts[" <-> ".join(candidate.venue_pair)] += 1
    return dict(sorted(counts.items()))


def _physical_date_map(observed: Iterable[ThresholdObservedQuote]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in observed:
        if not row.physical_event_date:
            continue
        out[row.market_id] = row.physical_event_date
        out[row.underlying_market_id] = row.physical_event_date
    return out


def _filter_exact_locks_by_physical_date(locks: Iterable[Any], observed: Iterable[ThresholdObservedQuote]) -> tuple[list[Any], int]:
    """Reject exact-key locks unless both market ids resolve to the same physical date."""
    date_by_market = _physical_date_map(observed)
    kept: list[Any] = []
    rejected = 0
    for lock in locks:
        yes_date = date_by_market.get(str(lock.yes_market_id))
        no_date = date_by_market.get(str(lock.no_market_id))
        if not yes_date or not no_date or yes_date != no_date:
            rejected += 1
            continue
        kept.append(lock)
    return kept, rejected


async def run_once(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_result, gemini, poly_raw, kalshi = await asyncio.gather(
            fetch_sportsbooks(http, api_key),
            fetch_gemini_mlb(http),
            fetch_polymarket_mlb(http),
            fetch_kalshi_mlb(http),
        )
        events, quota = sportsbook_result
        poly = full_game_only_events(poly_raw)
        priced_rows, errors, normalized_threshold_sides = await price_all_threshold_sides(http, gemini, poly, kalshi)

    received_ms = int(time.time() * 1000)
    all_retail = sportsbook_quotes(events, received_ms=received_ms)
    fanduel_quotes = [q for q in all_retail if q.venue == "sportsbook:fanduel"]
    start_by_event_id = _sportsbook_start_map(events)

    direct_observed = [_direct_observed(priced, side) for priced, side in priced_rows]
    fanduel_observed = [row for q in fanduel_quotes if (row := _fanduel_observed(q, start_by_event_id=start_by_event_id)) is not None]
    observed = direct_observed + fanduel_observed
    candidates = find_threshold_middles(observed, min_floor_profit=D("0.03"), max_start_skew_ms=300_000, max_quote_skew_ms=70_000)

    math_locks_3c = [x for x in candidates if x.classification == "SHADOW_LOCKED_MIDDLE_3C_PLUS"]
    positive_below = [x for x in candidates if x.classification == "SHADOW_POSITIVE_LOCK_BELOW_3C"]
    ev_shapes = [x for x in candidates if x.classification == "SPORTS_MIDDLE_EV_SHAPE_NOT_ARBITRAGE"]

    funded_quotes = [priced.quote for priced, _side in priced_rows] + fanduel_quotes
    raw_exact_locks = _dedupe(find_cross_venue_locks(
        funded_quotes,
        max_quote_skew_ms=70_000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    exact_locks, exact_cross_date_rejected = _filter_exact_locks_by_physical_date(raw_exact_locks, observed)

    result = {
        "status": "FOUR_VENUE_THRESHOLD_MIDDLE_SHADOW",
        "funded_venues": sorted(FUNDED_VENUES),
        "scope": "MLB_FULL_CONTEST_SPREAD_AND_GAME_TOTAL_THRESHOLD_FAMILIES",
        "physical_event_gate": "EXACT_MLB_EASTERN_DATE_REQUIRED_MARKET_ID_DATE_PREFERRED",
        "raw_events": {
            "gemini": len(gemini),
            "polymarket_full_game": len(poly),
            "kalshi": len(kalshi),
            "sportsbook": len(events),
        },
        "normalized_direct_threshold_sides": normalized_threshold_sides,
        "priced_direct_threshold_quotes": len(priced_rows),
        "priced_direct_by_venue": dict(Counter(priced.quote.venue for priced, _side in priced_rows)),
        "fanduel_threshold_quotes": len(fanduel_observed),
        "total_observed_threshold_quotes": len(observed),
        "observed_missing_physical_event_date": sum(1 for row in observed if not row.physical_event_date),
        "cross_threshold_candidate_counts": {
            "locked_3c_plus_math_only": len(math_locks_3c),
            "positive_lock_below_3c_math_only": len(positive_below),
            "middle_ev_shape_not_arbitrage": len(ev_shapes),
        },
        "locked_3c_plus_by_venue_pair": _venue_pair_counts(math_locks_3c),
        "top_locked_3c_plus_math_only": [x.as_dict() for x in math_locks_3c[:50]],
        "top_positive_below_3c_math_only": [x.as_dict() for x in positive_below[:30]],
        "top_middle_ev_shapes_not_arbitrage": [x.as_dict() for x in ev_shapes[:30]],
        "same_threshold_positive_locks_before_physical_date_gate": len(raw_exact_locks),
        "same_threshold_cross_date_or_unresolved_rejected": exact_cross_date_rejected,
        "same_threshold_positive_locks_math_only": len(exact_locks),
        "top_same_threshold_positive_locks_math_only": [x.as_dict() for x in exact_locks[:30]],
        "fetch_error_count": len(errors),
        "fetch_errors": errors[:50],
        "quota": quota,
        "pricing_scope": "Q1_TAKER_COSTS_WITH_EXISTING_VENUE_FEE_MODELS",
        "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_ANY_EXECUTION_CLASSIFICATION",
        "sportsbook_transport": "FANDUEL_MANUAL_ONLY",
        "execution_eligible_candidates": 0,
        "read_only": True,
        "order_entry": False,
        "realized_pnl_usd": "0",
        "realized_profit_credit": False,
    }
    logger.info("FOUR_VENUE_THRESHOLD_MIDDLE_SHADOW %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    print(json.dumps(asyncio.run(run_once(api_key)), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
