"""Rate-friendly q=1 pricing for the scope-safe three-venue MLB scanner.

Kalshi event responses with ``with_nested_markets=true`` already contain top-of-book
bid/ask prices and sizes. Reuse those snapshots instead of issuing one order-book
REST request per market on every cycle. This keeps the read-only discovery loop
fast without immediately running into Kalshi REST rate limits.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import time
from typing import Any, Iterable, Mapping

import httpx

from src.mlb_game_side_normalization import normalize_gemini_mlb, normalize_polymarket_mlb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.strict_mlb_prop_snapshot import KALSHI, POLY, TopBook, poly_top_book
from src.three_venue_mlb_price_scan import _eligible_keys, _gemini_contracts, _get, _kalshi_series
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote

D = Decimal
_SERIES_TTL_SECONDS = 300.0
_SERIES_CACHE: dict[str, tuple[Decimal, float]] = {}


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _price(value_dollars: Any, value_cents: Any) -> Decimal | None:
    dollars = _d(value_dollars)
    if dollars is not None:
        return dollars
    cents = _d(value_cents)
    return cents / D("100") if cents is not None else None


def _size(value_fp: Any, value: Any) -> Decimal | None:
    parsed = _d(value_fp)
    if parsed is not None:
        return parsed
    return _d(value)


def kalshi_nested_top_book(market: Mapping[str, Any]) -> TopBook | None:
    """Parse executable q=1 asks from a nested Kalshi Market object.

    Prefer direct ask fields. If an ask is absent, derive it from the opposite bid,
    which is equivalent for a binary $1 payout market.
    """
    yes_ask = _price(market.get("yes_ask_dollars"), market.get("yes_ask"))
    no_ask = _price(market.get("no_ask_dollars"), market.get("no_ask"))
    yes_depth = _size(market.get("yes_ask_size_fp"), market.get("yes_ask_size"))
    no_depth = _size(market.get("no_ask_size_fp"), market.get("no_ask_size"))

    if yes_ask is None or yes_depth is None or yes_depth <= 0:
        no_bid = _price(market.get("no_bid_dollars"), market.get("no_bid"))
        no_bid_size = _size(market.get("no_bid_size_fp"), market.get("no_bid_size"))
        if no_bid is not None and no_bid_size is not None and no_bid_size > 0:
            yes_ask = D("1") - no_bid
            yes_depth = no_bid_size

    if no_ask is None or no_depth is None or no_depth <= 0:
        yes_bid = _price(market.get("yes_bid_dollars"), market.get("yes_bid"))
        yes_bid_size = _size(market.get("yes_bid_size_fp"), market.get("yes_bid_size"))
        if yes_bid is not None and yes_bid_size is not None and yes_bid_size > 0:
            no_ask = D("1") - yes_bid
            no_depth = yes_bid_size

    if None in (yes_ask, yes_depth, no_ask, no_depth):
        return None
    assert yes_ask is not None and yes_depth is not None and no_ask is not None and no_depth is not None
    if not (D("0") < yes_ask <= D("1") and D("0") < no_ask <= D("1")):
        return None
    if yes_depth <= 0 or no_depth <= 0:
        return None
    return TopBook(yes_ask=yes_ask, yes_depth=yes_depth, no_ask=no_ask, no_depth=no_depth)


def _kalshi_markets(events: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in events:
        for market in event.get("markets") or []:
            if isinstance(market, Mapping) and market.get("ticker"):
                out[str(market["ticker"])] = market
    return out


async def _series_multipliers(
    http: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    series: list[str],
    errors: list[dict[str, str]],
) -> dict[str, Decimal]:
    now = time.monotonic()
    out: dict[str, Decimal] = {}
    missing: list[str] = []
    for ticker in series:
        cached = _SERIES_CACHE.get(ticker)
        if cached is not None and cached[1] > now:
            out[ticker] = cached[0]
        else:
            missing.append(ticker)

    if missing:
        results = await asyncio.gather(*(_get(http, semaphore, f"{KALSHI}/series/{ticker}") for ticker in missing))
        for ticker, (body, _received, error) in zip(missing, results):
            meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = _d(meta.get("fee_multiplier"))
            if fee_type in {"quadratic", "quadratic_with_maker_fees"} and multiplier is not None and D("0") <= multiplier <= D("100"):
                out[ticker] = multiplier
                _SERIES_CACHE[ticker] = (multiplier, now + _SERIES_TTL_SECONDS)
            else:
                errors.append({"venue":"kalshi","market_id":ticker,"error":error or f"bad_fee_metadata:{fee_type}:{multiplier}"})
    return out


async def price_counterparts_snapshot(
    http: httpx.AsyncClient,
    gemini_events: list[dict[str, Any]],
    poly_events: list[dict[str, Any]],
    kalshi_events: list[dict[str, Any]],
) -> tuple[list[PricedQuote], list[dict[str, str]], int]:
    rows = normalize_gemini_mlb(gemini_events) + normalize_polymarket_mlb(poly_events) + normalize_kalshi_mlb_eastern(kalshi_events)
    keys = _eligible_keys(rows)
    wanted = [row for row in rows if row.contract_key in keys]

    gcontracts = _gemini_contracts(gemini_events)
    kmarkets = _kalshi_markets(kalshi_events)
    ktoseries = _kalshi_series(kalshi_events)
    pids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "polymarket"})
    kids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "kalshi"})
    series = sorted({ktoseries.get(t, "") for t in kids if ktoseries.get(t, "")})

    sem = asyncio.Semaphore(20)
    p_results = await asyncio.gather(*(_get(http, sem, f"{POLY}/v1/markets/{market_id}/book") for market_id in pids))
    errors: list[dict[str, str]] = []
    multipliers = await _series_multipliers(http, sem, series, errors)

    pbooks: dict[str, tuple[TopBook, int]] = {}
    for market_id, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue":"polymarket","market_id":market_id,"error":error or "book_unparseable"})
        else:
            pbooks[market_id] = (book, received)

    priced: list[PricedQuote] = []
    snapshot_received = int(time.time() * 1000)
    for side in wanted:
        row: PricedQuote | None = None
        if side.identity.venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None:
                row = make_priced_quote(side, book, received_ms=snapshot_received, depth_source="event_prices_q1_probe_only")
            else:
                errors.append({"venue":"gemini","market_id":side.venue_market_id,"error":"buy_yes_no_unparseable"})
        elif side.identity.venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, depth_source="public_book_top_level")
        elif side.identity.venue == "kalshi":
            book = kalshi_nested_top_book(kmarkets.get(side.venue_market_id, {}))
            if book is None:
                errors.append({"venue":"kalshi","market_id":side.venue_market_id,"error":"nested_top_book_unparseable"})
            else:
                row = make_priced_quote(
                    side,
                    book,
                    received_ms=snapshot_received,
                    kalshi_fee_multiplier=multipliers.get(ktoseries.get(side.venue_market_id, "")),
                    depth_source="nested_event_market_top_of_book",
                )
        if row is not None:
            priced.append(row)

    return priced, errors, len(keys)
