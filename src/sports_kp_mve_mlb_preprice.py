"""MLB cross-venue prepricing for MVE candidate ranking.

The generator has already fetched every Kalshi component market needed for the
current MVE pass.  This module therefore reuses that in-memory market cache and
never refetches Kalshi MLB inventory during prepricing.  Only external Gemini and
Polymarket data/books are read here.

MLB full-game moneyline/spread/total use the existing strict normal-event
normalizers.  Polymarket player-prop prepricing is intentionally omitted from the
hot path; direct Gemini/Kalshi prop ranking is handled by the dedicated v19 helper.
All external replacements remain ranking-only and never grant execution authority.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import httpx

from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_mve_poly_preprice as nfl
from src.mlb_game_side_normalization import (
    NormalizedSide,
    normalize_gemini_mlb,
    normalize_kalshi_mlb,
    normalize_polymarket_mlb,
)
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import POLY, poly_top_book
from src.three_venue_mlb_price_scan import _gemini_contracts, _get
from src.three_venue_pricing_core import gemini_contract_book, make_priced_quote
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_polymarket_mlb

D = Decimal
ONE = D("1")
ZERO = D("0")
MAX_START_SKEW_MS = 5 * 60 * 1000
logger = logging.getLogger("kalshi_arb.sports_kp_mve_mlb_preprice")

MLB_GAME_PREFIXES = ("KXMLBGAME-", "KXMLBSPREAD-", "KXMLBTOTAL-")
MLB_PROP_PREFIXES = ("KXMLBHIT-", "KXMLBHR-", "KXMLBTB-", "KXMLBKS-", "KXMLBRBI-", "KXMLBHRR-")
_GAME_SERIES = {
    "KXMLBGAME-": "KXMLBGAME",
    "KXMLBSPREAD-": "KXMLBSPREAD",
    "KXMLBTOTAL-": "KXMLBTOTAL",
}


def _is_game(ticker: str) -> bool:
    return str(ticker or "").upper().startswith(MLB_GAME_PREFIXES)


def _is_prop(ticker: str) -> bool:
    return str(ticker or "").upper().startswith(MLB_PROP_PREFIXES)


def _key(leg: Any) -> tuple[str, str]:
    return str(leg.market_ticker), str(leg.side).lower()


def _install_if_cheaper(leg: Any, cost: Decimal, source: dict[str, Any]) -> bool:
    if cost <= ZERO or cost >= leg.hedge_all_in:
        return False
    key = _key(leg)
    previous = nfl._EFFECTIVE_COMPLEMENT_COST.get(key, leg.hedge_all_in)
    if cost >= previous:
        return False
    nfl._EFFECTIVE_COMPLEMENT_COST[key] = cost
    row = dict(source)
    row.update({
        "all_in_q1": str(cost),
        "savings_q1": str(leg.hedge_all_in - cost),
        "rules_certified": False,
        "execution_eligible": False,
    })
    nfl._EFFECTIVE_SOURCE[key] = row
    return True


def _series_for_event(event_ticker: str) -> str | None:
    upper = str(event_ticker or "").upper()
    for prefix, series in _GAME_SERIES.items():
        if upper.startswith(prefix):
            return series
    return None


def _cached_kalshi_sides(legs: list[Any]) -> tuple[dict[tuple[str, str], NormalizedSide], int]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    missing = 0
    for leg in legs:
        event_ticker = str(leg.event_ticker)
        series = _series_for_event(event_ticker)
        market = v8._MARKETS.get(str(leg.market_ticker))
        if series is None or not isinstance(market, Mapping):
            missing += 1
            continue
        grouped.setdefault((event_ticker, series), []).append(dict(market))

    synthetic = [
        {"event_ticker": event_ticker, "series_ticker": series, "markets": markets}
        for (event_ticker, series), markets in grouped.items()
    ]
    out: dict[tuple[str, str], NormalizedSide] = {}
    for side in normalize_kalshi_mlb(synthetic):
        out[(side.venue_market_id, side.side.upper())] = side
    return out, missing


async def _price_external_game_sides(
    target_by_key: dict[str, NormalizedSide],
) -> tuple[list[Any], list[dict[str, str]], int, int]:
    if not target_by_key:
        return [], [], 0, 0
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini, poly_raw = await asyncio.gather(fetch_gemini_mlb(http), fetch_polymarket_mlb(http))
        poly = full_game_only_events(poly_raw)
        external = normalize_gemini_mlb(gemini) + normalize_polymarket_mlb(poly)
        wanted: list[NormalizedSide] = []
        for side in external:
            target = target_by_key.get(side.contract_key)
            if target is None:
                continue
            if abs(side.identity.event.scheduled_start_ms - target.identity.event.scheduled_start_ms) > MAX_START_SKEW_MS:
                continue
            wanted.append(side)

        gcontracts = _gemini_contracts(gemini)
        pids = sorted({side.venue_market_id for side in wanted if side.identity.venue == "polymarket"})
        sem = asyncio.Semaphore(8)
        p_results = await asyncio.gather(*(_get(http, sem, f"{POLY}/v1/markets/{slug}/book") for slug in pids)) if pids else []

    errors: list[dict[str, str]] = []
    pbooks: dict[str, tuple[Any, int]] = {}
    for slug, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "polymarket", "market_id": slug, "error": error or "book_unparseable"})
        else:
            pbooks[slug] = (book, received)

    priced: list[Any] = []
    now_ms = int(time.time() * 1000)
    for side in wanted:
        row = None
        if side.identity.venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None:
                row = make_priced_quote(side, book, received_ms=now_ms, depth_source="event_prices_q1_probe_only")
        elif side.identity.venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, depth_source="public_book_top_level")
        if row is not None:
            priced.append(row)
    return priced, errors, len(gemini), len(poly)


async def _game_preprice(legs: list[Any]) -> dict[str, Any]:
    wanted = [leg for leg in legs if _is_game(str(leg.market_ticker))]
    if not wanted:
        return {"legs": 0, "counterpart_keys": 0, "improved": 0, "best_saving": "0"}

    cached_sides, missing_cache = _cached_kalshi_sides(wanted)
    target_for_leg: dict[tuple[str, str], NormalizedSide] = {}
    target_by_key: dict[str, NormalizedSide] = {}
    for leg in wanted:
        side = cached_sides.get((str(leg.market_ticker), str(leg.hedge_side).upper()))
        if side is None:
            continue
        target_for_leg[(str(leg.market_ticker), str(leg.hedge_side).upper())] = side
        target_by_key[side.contract_key] = side

    try:
        priced, errors, gemini_events, poly_events = await _price_external_game_sides(target_by_key)
    except Exception as exc:
        return {
            "legs": len(wanted), "counterpart_keys": len(target_by_key), "improved": 0,
            "best_saving": "0", "external_error": type(exc).__name__,
            "missing_cached_markets": missing_cache,
        }

    external_by_key: dict[str, list[Any]] = {}
    for row in priced:
        external_by_key.setdefault(row.quote.settlement_key, []).append(row)

    improved = 0
    best_saving = ZERO
    by_venue = {"polymarket": 0, "gemini": 0}
    for leg in wanted:
        target = target_for_leg.get((str(leg.market_ticker), str(leg.hedge_side).upper()))
        if target is None:
            continue
        candidates = external_by_key.get(target.contract_key) or []
        best = None
        for row in candidates:
            q = row.quote
            if q.yes_depth < ONE or not (ZERO < q.yes_cost < ONE):
                continue
            if best is None or q.yes_cost < best.quote.yes_cost:
                best = row
        if best is None:
            continue
        cost = best.quote.yes_cost
        source = {
            "venue": best.quote.venue,
            "market_id": best.quote.market_id,
            "settlement_key": best.quote.settlement_key,
            "depth_q1": str(best.quote.yes_depth),
            "depth_source": best.depth_source,
            "source_side": best.source_side,
            "scope": "MLB_FULL_GAME_EXACT_NORMAL_EVENT_IDENTITY",
            "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        }
        if _install_if_cheaper(leg, cost, source):
            improved += 1
            by_venue[best.quote.venue] = by_venue.get(best.quote.venue, 0) + 1
            best_saving = max(best_saving, leg.hedge_all_in - cost)

    return {
        "legs": len(wanted), "counterpart_keys": len(target_by_key), "priced_quotes": len(priced),
        "pricing_errors": len(errors), "improved": improved, "improved_by_venue": by_venue,
        "best_saving": str(best_saving), "missing_cached_markets": missing_cache,
        "external_inventory": {"gemini": gemini_events, "polymarket": poly_events},
        "kalshi_refetches": 0,
    }


async def preprice_hook(collection: dict[str, Any], event_legs: dict[str, list[Any]]) -> dict[str, list[Any]]:
    # NFL hook owns cache clearing and exact NFL orientation semantics.
    event_legs = await nfl.preprice_hook(collection, event_legs)
    legs = [leg for rows in event_legs.values() for leg in rows]
    game = await _game_preprice(legs)
    prop = {
        "legs": sum(1 for leg in legs if _is_prop(str(leg.market_ticker))),
        "status": "DIRECT_GEMINI_PREPRICE_HANDLED_BY_V19",
        "kalshi_refetches": 0,
        "improved": 0,
        "best_saving": "0",
    }
    logger.warning("SPORTS_MVE_MLB_PREPRICE %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "mlb_game": game,
        "mlb_prop": prop,
        "ranking_only": True,
        "cross_venue_order_entry": False,
        "prop_guaranteed_arb": False,
    }, separators=(",", ":"), default=str))
    return event_legs
