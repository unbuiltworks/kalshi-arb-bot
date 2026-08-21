"""MLB cross-venue prepricing for MVE candidate ranking.

Extends the v16 NFL ranking cache with:
- MLB full-game moneyline/spread/total using the existing three-venue normalizer
  and conservative q=1 snapshot pricer;
- MLB player props using the existing strict Polymarket/Kalshi player+line+game
  matcher and public q=1 Polymarket books.

This is selection/ranking only. It never mutates a Kalshi Leg, never grants rule
certification, and never exposes order entry. MLB prop rule differences remain
explicitly non-executable for K<->P guaranteed-arb purposes.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
from typing import Any

import httpx

from src import sports_kp_mve_poly_preprice as nfl
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import (
    POLY,
    _fetch_kalshi_events,
    _fetch_poly_mlb,
    discover_strict_candidates,
    poly_top_book,
    polymarket_fee,
)
from src.three_venue_mlb_snapshot_pricing import price_counterparts_snapshot
from src.universal_three_venue_sports import (
    fetch_gemini_mlb,
    fetch_kalshi_mlb,
    fetch_polymarket_mlb,
)

D = Decimal
ONE = D("1")
ZERO = D("0")
logger = logging.getLogger("kalshi_arb.sports_kp_mve_mlb_preprice")

MLB_GAME_PREFIXES = ("KXMLBGAME-", "KXMLBSPREAD-", "KXMLBTOTAL-")
MLB_PROP_PREFIXES = ("KXMLBHIT-", "KXMLBHR-", "KXMLBTB-", "KXMLBKS-", "KXMLBRBI-", "KXMLBHRR-")


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


async def _game_preprice(legs: list[Any]) -> dict[str, Any]:
    wanted = [leg for leg in legs if _is_game(str(leg.market_ticker))]
    if not wanted:
        return {"legs": 0, "counterpart_keys": 0, "improved": 0, "best_saving": "0"}

    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini, poly_raw, kalshi = await asyncio.gather(
            fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
        )
        poly = full_game_only_events(poly_raw)
        priced, errors, key_count = await price_counterparts_snapshot(http, gemini, poly, kalshi)

    kalshi_by_market_side: dict[tuple[str, str], Any] = {}
    external_by_key: dict[str, list[Any]] = {}
    for row in priced:
        q = row.quote
        if q.venue == "kalshi":
            ticker = q.market_id.split("#", 1)[0]
            kalshi_by_market_side[(ticker, row.source_side.upper())] = row
        elif q.venue in {"polymarket", "gemini"}:
            external_by_key.setdefault(q.settlement_key, []).append(row)

    improved = 0
    best_saving = ZERO
    by_venue = {"polymarket": 0, "gemini": 0}
    for leg in wanted:
        hedge_side = str(leg.hedge_side).upper()
        krow = kalshi_by_market_side.get((str(leg.market_ticker), hedge_side))
        if krow is None:
            continue
        candidates = external_by_key.get(krow.quote.settlement_key) or []
        best = None
        for row in candidates:
            q = row.quote
            # YES cost is the cost of the canonical proposition represented by
            # this normalized side, which is exactly the Kalshi physical hedge
            # side selected above.
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
        "legs": len(wanted), "counterpart_keys": key_count, "priced_quotes": len(priced),
        "pricing_errors": len(errors), "improved": improved, "improved_by_venue": by_venue,
        "best_saving": str(best_saving),
    }


async def _prop_preprice(legs: list[Any]) -> dict[str, Any]:
    wanted = [leg for leg in legs if _is_prop(str(leg.market_ticker))]
    if not wanted:
        return {"legs": 0, "strict_matches": 0, "books": 0, "improved": 0, "best_saving": "0"}

    wanted_tickers = {str(leg.market_ticker) for leg in wanted}
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(_fetch_poly_mlb(http), _fetch_kalshi_events(http))
        strict = [c for c in discover_strict_candidates(poly_markets, kalshi_events) if c.kalshi_ticker in wanted_tickers]
        # Ambiguous external matches fail closed.
        by_ticker: dict[str, list[Any]] = {}
        for candidate in strict:
            by_ticker.setdefault(candidate.kalshi_ticker, []).append(candidate)
        unique = {ticker: rows[0] for ticker, rows in by_ticker.items() if len(rows) == 1}
        try:
            max_books = max(8, min(int(os.getenv("MVE_PREPRICE_MAX_MLB_PROP_BOOKS", "32")), 64))
        except Exception:
            max_books = 32
        slugs = [row.poly_slug for row in unique.values()][:max_books]
        sem = asyncio.Semaphore(8)

        async def book(slug: str):
            async with sem:
                r = await http.get(f"{POLY}/v1/markets/{slug}/book")
                if r.status_code != 200:
                    return slug, None
                return slug, poly_top_book(r.json() if r.content else {})

        books = dict(await asyncio.gather(*(book(slug) for slug in slugs))) if slugs else {}

    improved = 0
    best_saving = ZERO
    for leg in wanted:
        candidate = unique.get(str(leg.market_ticker))
        if candidate is None:
            continue
        book = books.get(candidate.poly_slug)
        if book is None:
            continue
        hedge_side = str(leg.hedge_side).lower()
        if hedge_side == "yes":
            price, depth = book.yes_ask, book.yes_depth
        elif hedge_side == "no":
            price, depth = book.no_ask, book.no_depth
        else:
            continue
        if depth < ONE or not (ZERO < price < ONE):
            continue
        cost = price + polymarket_fee(1, price)
        source = {
            "venue": "polymarket",
            "market_id": candidate.poly_slug,
            "depth_q1": str(depth),
            "player": candidate.player,
            "threshold": str(candidate.threshold),
            "family": candidate.family,
            "start_skew_seconds": candidate.start_skew_seconds,
            "scope": "MLB_PROP_STRICT_PLAYER_LINE_GAME_MATCH",
            "rule_gate": "KNOWN_KALSHI_POLY_RULE_DIFFERENCES_RANKING_ONLY",
        }
        if _install_if_cheaper(leg, cost, source):
            improved += 1
            best_saving = max(best_saving, leg.hedge_all_in - cost)

    return {
        "legs": len(wanted), "strict_matches": len(strict), "unique_matches": len(unique),
        "books": sum(book is not None for book in books.values()), "improved": improved,
        "best_saving": str(best_saving),
    }


async def preprice_hook(collection: dict[str, Any], event_legs: dict[str, list[Any]]) -> dict[str, list[Any]]:
    # NFL hook owns cache clearing and exact NFL orientation semantics.
    event_legs = await nfl.preprice_hook(collection, event_legs)
    legs = [leg for rows in event_legs.values() for leg in rows]
    game, prop = await asyncio.gather(_game_preprice(legs), _prop_preprice(legs))
    logger.warning("SPORTS_MVE_MLB_PREPRICE %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "mlb_game": game,
        "mlb_prop": prop,
        "ranking_only": True,
        "cross_venue_order_entry": False,
        "prop_guaranteed_arb": False,
    }, separators=(",", ":"), default=str))
    return event_legs
