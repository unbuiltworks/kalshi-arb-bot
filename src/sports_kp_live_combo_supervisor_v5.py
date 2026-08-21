"""Production combo supervisor v5: Polymarket-first, rate-limited Kalshi MVE generation.

This layer keeps v4's action-only notifications and the fail-closed q=1 executor,
while fixing two production issues observed on 2026-08-21:

1. Kalshi exposes YES-side book sizes.  A NO ask is the complement of the YES bid,
   so executable NO depth must use ``yes_bid_size_fp`` (or legacy yes_bid_size),
   not a nonexistent ``no_ask_size_fp`` field.
2. MVE generation previously burst dozens of ``/markets?event_ticker=...`` calls.
   Event-market reads are now cached, paced, bounded per generator run, and retry
   429s with Retry-After/exponential backoff.

The supervisor still inventories Polymarket before every Kalshi generation cycle.
Polymarket live sport families are used to prioritize Kalshi collections, but are
not a hard gate: same-Kalshi scalar-safe synthetic arbitrage can be valid even when
there is no matching Polymarket event.  All order-entry and after-fee q=1 safety
checks remain in the existing live executor and are revalidated from fresh data.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v4 as v4
from src import sports_kp_mve_combo_generator as generator
from src import sports_kp_mve_same_venue_scan as existing_scan
from src import sports_kp_mve_same_venue_live_q1 as live_base

logger = logging.getLogger("kalshi_arb.sports_kp_live_combo_supervisor_v5")
base = v4.base

# ---- Correct Kalshi complement-side depth mapping -------------------------

def _fixed_leg_from_market(
    market: dict[str, Any], event_ticker: str, side: str, horizon: datetime
) -> generator.Leg | None:
    side = side.lower()
    if side not in {"yes", "no"}:
        return None
    if str(market.get("status") or "").lower() not in {"active", "open"}:
        return None
    close = generator._parse_dt(
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    if close is None or close <= datetime.now(close.tzinfo) or close > horizon:
        return None
    ticker = str(market.get("ticker") or "")
    if not ticker:
        return None

    if side == "yes":
        # Buying NO consumes resting YES bids. Kalshi publishes YES-side sizes.
        hedge_side = "no"
        price = generator._d(market.get("no_ask_dollars"))
        depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        hedge_side = "yes"
        price = generator._d(market.get("yes_ask_dollars"))
        depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))

    if (
        price is None
        or depth is None
        or not (generator.ZERO < price < generator.ONE)
        or depth < generator.ONE
    ):
        return None
    return generator.Leg(
        event_ticker,
        ticker,
        side,
        hedge_side,
        price,
        generator._fee(price),
        depth,
        close,
    )


def _fixed_hedge_quote(market: dict[str, Any], selected: str) -> dict[str, Any]:
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        price = existing_scan._d(market.get("yes_ask_dollars"))
        depth = existing_scan._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    elif hedge == "no":
        price = existing_scan._d(market.get("no_ask_dollars"))
        depth = existing_scan._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        price = depth = None
    ok = bool(
        price is not None
        and existing_scan.D("0") < price < existing_scan.ONE
        and depth is not None
        and depth >= existing_scan.ONE
        and str(market.get("status") or "").lower() in {"active", "open"}
    )
    return {
        "hedge_side": hedge,
        "price": price,
        "depth": depth,
        "fee": existing_scan._fee(price) if price is not None else None,
        "ok": ok,
    }


def _fixed_make_hedge(market: dict[str, Any], selected: str) -> live_base.HedgePlan | None:
    ticker = str(market.get("ticker") or "")
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        price = live_base._d(market.get("yes_ask_dollars"))
        depth = live_base._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        yes_limit = price
        native = "YES"
    elif hedge == "no":
        price = live_base._d(market.get("no_ask_dollars"))
        depth = live_base._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
        yes_limit = live_base.ONE - price if price is not None else None
        native = "NO"
    else:
        return None
    if (
        not ticker
        or price is None
        or yes_limit is None
        or depth is None
        or not (live_base.ZERO < price < live_base.ONE)
        or depth < live_base.ONE
        or str(market.get("status") or "").lower() not in {"active", "open"}
    ):
        return None
    leg = live_base.ExecutionLeg(
        venue="kalshi",
        market_id=ticker,
        normalized_outcome=native,
        native_outcome=native,
        raw_outcome_price=price,
        yes_book_limit=yes_limit,
        source_side="CONTRACT",
    )
    return live_base.HedgePlan(
        ticker, selected, hedge, price, depth, live_base._fee(price), leg
    )


generator._leg_from_market = _fixed_leg_from_market
existing_scan._hedge_quote = _fixed_hedge_quote
live_base._make_hedge = _fixed_make_hedge

# ---- Polymarket-first context + bounded Kalshi reads ----------------------

_ORIGINAL_POLY_INVENTORY = base.poly_live_inventory
_ORIGINAL_GENERATE = base.generate_mves
_ORIGINAL_OPEN_COLLECTIONS = generator._open_collections

_LATEST_POLY: dict[str, Any] = {}
_COLLECTION_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])
_EVENT_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_EVENT_REQUEST_LOCK = asyncio.Lock()
_LAST_EVENT_REQUEST_AT = 0.0
_GENERATOR_ACTIVE = False
_RUN_EVENT_REQUESTS = 0
_RUN_EVENT_CACHE_HITS = 0
_RUN_429S = 0
_RUN_BUDGET_SKIPS = 0


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(float(os.getenv(name, str(default))), hi))
    except Exception:
        return default


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(os.getenv(name, str(default))), hi))
    except Exception:
        return default


def _poly_family_tokens() -> set[str]:
    text_parts: list[str] = []
    for event in _LATEST_POLY.get("events") or []:
        if not isinstance(event, dict):
            continue
        for key in ("series_slug", "subcategory", "category", "title", "ticker", "slug"):
            text_parts.append(str(event.get(key) or "").upper())
        for market in event.get("markets") or []:
            if isinstance(market, dict):
                text_parts.append(str(market.get("sportsMarketType") or "").upper())
                text_parts.append(str(market.get("sportsMarketTypeV2") or "").upper())
    text = " ".join(text_parts)
    families: set[str] = set()
    mapping = {
        "NFL": ("NFL", "AMERICAN FOOTBALL"),
        "MLB": ("MLB", "BASEBALL"),
        "NBA": ("NBA", "BASKETBALL"),
        "WNBA": ("WNBA",),
        "NHL": ("NHL", "HOCKEY"),
        "ATP": ("ATP",),
        "WTA": ("WTA",),
        "TENNIS": ("TENNIS", "ITF"),
        "SOCCER": ("SOCCER", "PREMIER", "LIGA", "SERIE", "UEFA", "MLS", "FOOTBALL"),
        "UFC": ("UFC", "MMA"),
    }
    for family, needles in mapping.items():
        if any(needle in text for needle in needles):
            families.add(family)
    return families


def _prioritize_collections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families = _poly_family_tokens()
    if not families:
        return rows

    def score(row: dict[str, Any]) -> tuple[int, str]:
        text = " ".join(
            str(row.get(key) or "").upper()
            for key in ("collection_ticker", "series_ticker", "title", "description")
        )
        match = any(family in text for family in families)
        return (0 if match else 1, str(row.get("collection_ticker") or ""))

    return sorted(rows, key=score)


async def _poly_inventory_first() -> dict[str, Any]:
    global _LATEST_POLY
    result = await _ORIGINAL_POLY_INVENTORY()
    _LATEST_POLY = result if isinstance(result, dict) else {}
    return result


async def _cached_open_collections(client: Any) -> list[dict[str, Any]]:
    global _COLLECTION_CACHE
    loop = asyncio.get_running_loop()
    now = loop.time()
    ttl = _float_env("MVE_COLLECTION_CACHE_SECONDS", 600.0, 30.0, 1800.0)
    cached_at, cached_rows = _COLLECTION_CACHE
    if cached_rows and now - cached_at <= ttl:
        return _prioritize_collections([dict(row) for row in cached_rows])
    rows = await _ORIGINAL_OPEN_COLLECTIONS(client)
    rows = [dict(row) for row in rows if isinstance(row, dict)]
    _COLLECTION_CACHE = (loop.time(), rows)
    return _prioritize_collections([dict(row) for row in rows])


async def _paced_event_markets(client: Any, event_ticker: str) -> list[dict[str, Any]]:
    global _LAST_EVENT_REQUEST_AT, _RUN_EVENT_REQUESTS, _RUN_EVENT_CACHE_HITS
    global _RUN_429S, _RUN_BUDGET_SKIPS

    loop = asyncio.get_running_loop()
    now = loop.time()
    cache_ttl = _float_env("MVE_EVENT_MARKET_CACHE_SECONDS", 45.0, 5.0, 180.0)
    cached = _EVENT_CACHE.get(event_ticker)
    if cached and now - cached[0] <= cache_ttl:
        _RUN_EVENT_CACHE_HITS += 1
        return [dict(row) for row in cached[1]]

    request_budget = _int_env("MVE_GENERATOR_MAX_EVENT_MARKET_REQUESTS_PER_RUN", 24, 4, 80)
    if _GENERATOR_ACTIVE and _RUN_EVENT_REQUESTS >= request_budget:
        _RUN_BUDGET_SKIPS += 1
        return []

    min_spacing = _float_env("MVE_KALSHI_REQUEST_SPACING_SECONDS", 0.20, 0.05, 2.0)
    max_attempts = _int_env("MVE_KALSHI_429_MAX_ATTEMPTS", 3, 1, 5)
    params = {"event_ticker": event_ticker, "limit": 1000, "mve_filter": "exclude"}

    for attempt in range(max_attempts):
        async with _EVENT_REQUEST_LOCK:
            since_last = loop.time() - _LAST_EVENT_REQUEST_AT
            if since_last < min_spacing:
                await asyncio.sleep(min_spacing - since_last)
            response = await client.get("/markets", params=params)
            _LAST_EVENT_REQUEST_AT = loop.time()
            if _GENERATOR_ACTIVE:
                _RUN_EVENT_REQUESTS += 1

        if response.status_code == 429:
            _RUN_429S += 1
            retry_after = response.headers.get("Retry-After")
            try:
                backoff = float(retry_after) if retry_after else 0.75 * (2**attempt)
            except Exception:
                backoff = 0.75 * (2**attempt)
            await asyncio.sleep(max(0.25, min(backoff, 6.0)))
            continue
        if response.status_code != 200:
            return []

        body = response.json() if response.content else {}
        rows = (
            [dict(row) for row in (body.get("markets") or []) if isinstance(row, dict)]
            if isinstance(body, dict)
            else []
        )
        _EVENT_CACHE[event_ticker] = (loop.time(), rows)
        return [dict(row) for row in rows]
    return []


async def _bounded_generate_after_poly() -> dict[str, Any]:
    global _GENERATOR_ACTIVE, _RUN_EVENT_REQUESTS, _RUN_EVENT_CACHE_HITS
    global _RUN_429S, _RUN_BUDGET_SKIPS

    _RUN_EVENT_REQUESTS = 0
    _RUN_EVENT_CACHE_HITS = 0
    _RUN_429S = 0
    _RUN_BUDGET_SKIPS = 0
    _GENERATOR_ACTIVE = True
    try:
        result = await _ORIGINAL_GENERATE()
    finally:
        _GENERATOR_ACTIVE = False

    logger.warning(
        "SPORTS_MVE_REQUEST_BUDGET %s",
        json.dumps(
            {
                "poly_first": True,
                "poly_live_event_count": _LATEST_POLY.get("live_event_count"),
                "poly_families": sorted(_poly_family_tokens()),
                "kalshi_event_market_requests": _RUN_EVENT_REQUESTS,
                "kalshi_event_market_cache_hits": _RUN_EVENT_CACHE_HITS,
                "kalshi_429s": _RUN_429S,
                "request_budget_skips": _RUN_BUDGET_SKIPS,
                "max_event_market_requests_per_run": _int_env(
                    "MVE_GENERATOR_MAX_EVENT_MARKET_REQUESTS_PER_RUN", 24, 4, 80
                ),
                "order_entry": False,
            },
            separators=(",", ":"),
            default=str,
        ),
    )
    return result


# base.run resolves these globals from the v2 supervisor module at runtime.
base.poly_live_inventory = _poly_inventory_first
base.generate_mves = _bounded_generate_after_poly
generator._open_collections = _cached_open_collections
generator._event_markets = _paced_event_markets

run = base.run
main = base.main


if __name__ == "__main__":
    main()
