"""Read-only Polymarket prepricing for NFL MVE candidate selection.

This module changes only which Kalshi MVE baskets are *manufactured first*.
It never mutates Kalshi Leg execution facts and never grants cross-venue rule
certification.  Exact live books are fetched for a bounded set of counterpart
legs with the largest indicative savings, then a shadow effective-cost cache is
used by the candidate ranker before Kalshi market creation/RFQ.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
from typing import Any, Mapping

import httpx

from src import sports_kp_mve_combo_generator as generator
from src import sports_kp_mve_combo_generator_v2 as generator_v2
from src import sports_kp_mve_poly_pregame_shadow as poly
from src.polymarket_kalshi_shadow import polymarket_taker_fee
from src.polymarket_kalshi_shadow_v2 import PolyBookStore, _fetch_poly_book
from src.sports_kp_mve_cross_venue_shadow import _kalshi_identity_key

D = Decimal
ONE = D("1")
ZERO = D("0")
logger = logging.getLogger("kalshi_arb.sports_kp_mve_poly_preprice")

# (Kalshi market ticker, selected MVE side) -> exact q=1 complement cost.
_EFFECTIVE_COMPLEMENT_COST: dict[tuple[str, str], Decimal] = {}
_EFFECTIVE_SOURCE: dict[tuple[str, str], dict[str, Any]] = {}


def _max_books() -> int:
    try:
        return max(8, min(int(os.getenv("MVE_PREPRICE_MAX_POLY_BOOKS", "48")), 96))
    except Exception:
        return 48


def _d(v: Any) -> Decimal | None:
    try:
        out = D(str(v))
    except Exception:
        return None
    return out if out.is_finite() else None


def _fee(price: Decimal) -> Decimal:
    return polymarket_taker_fee(1, price, poly.POLY_FEE_RATE)


def _flip(side: str) -> str:
    return "no" if side == "yes" else "yes"


def _market_side_quote(market: Mapping[str, Any], book_side: str) -> tuple[Decimal, Decimal] | None:
    """Indicative ask/depth from event metadata, only for prioritizing book fetches."""
    want_long = book_side == "yes"
    hits: list[tuple[Decimal, Decimal]] = []
    for side in market.get("marketSides") or []:
        if not isinstance(side, Mapping) or bool(side.get("long")) != want_long or side.get("tradable") is False:
            continue
        quote = side.get("quote") if isinstance(side.get("quote"), Mapping) else {}
        price = _d(quote.get("value"))
        if price is None:
            price = _d(side.get("price"))
        if price is None or not (ZERO < price < ONE):
            continue
        # Event metadata does not always expose depth. Use 1 as an indicative
        # placeholder only; exact book depth is required below before caching.
        hits.append((price, ONE))
    return hits[0] if len(hits) == 1 else None


def effective_complement_cost(leg: Any) -> Decimal:
    key = (str(leg.market_ticker), str(leg.side).lower())
    return _EFFECTIVE_COMPLEMENT_COST.get(key, leg.hedge_all_in)


def source_for_leg(leg: Any) -> dict[str, Any] | None:
    return _EFFECTIVE_SOURCE.get((str(leg.market_ticker), str(leg.side).lower()))


async def preprice_hook(
    collection: dict[str, Any], event_legs: dict[str, list[Any]]
) -> dict[str, list[Any]]:
    _EFFECTIVE_COMPLEMENT_COST.clear()
    _EFFECTIVE_SOURCE.clear()

    legs = [leg for rows in event_legs.values() for leg in rows]
    keyed: dict[str, tuple[Any, ...]] = {}
    for leg in legs:
        key = _kalshi_identity_key(str(leg.event_ticker), str(leg.market_ticker))
        if key is not None:
            keyed[str(leg.market_ticker)] = key
    if not keyed:
        return event_legs

    try:
        horizon = int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24"))
    except Exception:
        horizon = 24
    events = await poly._pregame_events(horizon)

    # Canonical key -> exactly one Polymarket market. Ambiguity fails closed.
    poly_rows: list[tuple[tuple[Any, ...], str, bool, Mapping[str, Any]]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            parsed = poly._poly_identity(event, market)
            if parsed is None:
                continue
            pkey, slug = parsed
            poly_rows.append((pkey, slug, poly._proposition_direct_to_book(pkey), market))

    matches: dict[str, tuple[str, bool, Mapping[str, Any]]] = {}
    for mt, kkey in keyed.items():
        found = [(slug, direct, market) for pkey, slug, direct, market in poly_rows if poly._key_compatible(kkey, pkey)]
        unique = {(slug, direct) for slug, direct, _market in found}
        if len(unique) == 1:
            slug, direct = next(iter(unique))
            market = next(m for s, d, m in found if s == slug and d == direct)
            matches[mt] = (slug, direct, market)

    # Rank potential exact-book fetches by metadata-indicated savings. A single
    # slug can service both selected-side variants, so dedupe at slug level.
    slug_priority: dict[str, Decimal] = {}
    indicative_rows = 0
    for leg in legs:
        matched = matches.get(str(leg.market_ticker))
        if matched is None:
            continue
        slug, direct, market = matched
        hedge_side = str(leg.hedge_side).lower()
        book_side = hedge_side if direct else _flip(hedge_side)
        indicative = _market_side_quote(market, book_side)
        if indicative is None:
            continue
        indicative_rows += 1
        price, _ = indicative
        poly_cost = price + _fee(price)
        saving = max(ZERO, leg.hedge_all_in - poly_cost)
        if saving > slug_priority.get(slug, ZERO):
            slug_priority[slug] = saving

    wanted_slugs = [
        slug for slug, saving in sorted(slug_priority.items(), key=lambda kv: kv[1], reverse=True)
        if saving > ZERO
    ][:_max_books()]

    store = PolyBookStore()
    if wanted_slugs:
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=8.0) as http:
            await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in wanted_slugs])

    improved = 0
    total_savings = ZERO
    best_saving = ZERO
    for leg in legs:
        matched = matches.get(str(leg.market_ticker))
        if matched is None:
            continue
        slug, direct, _market = matched
        pq = store.quote(slug, max_age_ms=15000)
        if pq is None:
            continue
        hedge_side = str(leg.hedge_side).lower()
        book_side = hedge_side if direct else _flip(hedge_side)
        if book_side == "yes":
            price, depth = pq.yes_ask, pq.yes_depth
        else:
            price, depth = pq.no_ask, pq.no_depth
        if depth < ONE or not (ZERO < price < ONE):
            continue
        exact_cost = price + _fee(price)
        key = (str(leg.market_ticker), str(leg.side).lower())
        chosen = min(leg.hedge_all_in, exact_cost)
        _EFFECTIVE_COMPLEMENT_COST[key] = chosen
        if exact_cost < leg.hedge_all_in:
            saving = leg.hedge_all_in - exact_cost
            improved += 1
            total_savings += saving
            best_saving = max(best_saving, saving)
            _EFFECTIVE_SOURCE[key] = {
                "venue": "polymarket",
                "slug": slug,
                "proposition_direct_to_book": direct,
                "book_side": book_side,
                "all_in_q1": str(exact_cost),
                "depth_q1": str(depth),
                "savings_q1": str(saving),
                "rules_certified": False,
                "execution_eligible": False,
            }

    logger.warning(
        "SPORTS_MVE_PREPRICE %s",
        json.dumps({
            "collection": collection.get("collection_ticker"),
            "kalshi_legs_seen": len(legs),
            "exact_identity_matches": len(matches),
            "indicative_rows": indicative_rows,
            "exact_books_requested": len(wanted_slugs),
            "exact_books_loaded": store.quote_count,
            "legs_with_cheaper_poly_complement": improved,
            "aggregate_leg_savings_q1": str(total_savings),
            "best_single_leg_savings_q1": str(best_saving),
            "ranking_only": True,
            "execution_eligible": False,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )
    return event_legs


# Default registration is intentionally explicit in the production v16 wrapper.
