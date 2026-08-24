"""Read-only q=1 MLB lock scanner across Gemini, Polymarket US, and Kalshi.

Polymarket is fail-closed to the existing main full-game scope guard before any
identity matching or pricing. Public book reads are deliberately low-concurrency and
429-aware so this monitor cannot starve live execution of venue request capacity.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal
import json
import logging
import time
from typing import Any, Iterable, Mapping

import httpx

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import NormalizedSide, normalize_gemini_mlb, normalize_polymarket_mlb
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import KALSHI, POLY, kalshi_top_book, poly_top_book
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

D = Decimal
logger = logging.getLogger("kalshi_arb.three_venue_mlb_price_scan")


def _d(value: Any) -> Decimal | None:
    try: out = D(str(value))
    except Exception: return None
    return out if out.is_finite() else None


def _eligible_keys(rows: list[NormalizedSide]) -> set[str]:
    groups: dict[str, list[NormalizedSide]] = defaultdict(list)
    for row in rows: groups[row.contract_key].append(row)
    out: set[str] = set()
    for key, matches in groups.items():
        if len({row.identity.venue for row in matches}) < 2: continue
        starts = [row.identity.event.scheduled_start_ms for row in matches]
        if max(starts) - min(starts) <= 5 * 60 * 1000: out.add(key)
    return out


def _gemini_contracts(events: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in events:
        for contract in event.get("contracts") or []:
            if isinstance(contract, Mapping):
                symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or "")
                if symbol: out[symbol] = contract
    return out


def _kalshi_series(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for event in events:
        series = str(event.get("series_ticker") or "")
        for market in event.get("markets") or []:
            if isinstance(market, Mapping) and market.get("ticker"): out[str(market["ticker"])] = series
    return out


async def _get(http: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str, **kwargs: Any) -> tuple[dict[str, Any] | None, int, str | None]:
    async with semaphore:
        last_error: str | None = None
        for attempt in range(4):
            try:
                response = await http.get(url, **kwargs)
                received = int(time.time() * 1000)
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after", "").strip()
                    try: header = float(retry_after) if retry_after else 0.0
                    except ValueError: header = 0.0
                    await asyncio.sleep(max(header, min(0.35 * (2 ** attempt), 2.0)))
                    last_error = "HTTPStatusError:429 Too Many Requests"
                    continue
                response.raise_for_status()
                body = response.json()
                return (body if isinstance(body, dict) else {}), received, None
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{str(exc)[:180]}"
                if attempt + 1 < 4: await asyncio.sleep(0.20 * (attempt + 1))
        return None, int(time.time() * 1000), last_error


async def price_counterparts(http: httpx.AsyncClient, gemini_events: list[dict[str, Any]], poly_events: list[dict[str, Any]], kalshi_events: list[dict[str, Any]]) -> tuple[list[PricedQuote], list[dict[str, str]], int]:
    rows = normalize_gemini_mlb(gemini_events) + normalize_polymarket_mlb(poly_events) + normalize_kalshi_mlb_eastern(kalshi_events)
    keys = _eligible_keys(rows); wanted = [row for row in rows if row.contract_key in keys]
    gcontracts = _gemini_contracts(gemini_events); ktoseries = _kalshi_series(kalshi_events)
    pids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "polymarket"})
    kids = sorted({row.venue_market_id for row in wanted if row.identity.venue == "kalshi"})
    series = sorted({ktoseries.get(t, "") for t in kids if ktoseries.get(t, "")})
    sem = asyncio.Semaphore(4)
    p_results, k_results, s_results = await asyncio.gather(
        asyncio.gather(*(_get(http, sem, f"{POLY}/v1/markets/{x}/book") for x in pids)),
        asyncio.gather(*(_get(http, sem, f"{KALSHI}/markets/{x}/orderbook", params={"depth": 1}) for x in kids)),
        asyncio.gather(*(_get(http, sem, f"{KALSHI}/series/{x}") for x in series)),
    )
    errors: list[dict[str, str]] = []; pbooks = {}; kbooks = {}
    for market_id, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None: errors.append({"venue":"polymarket","market_id":market_id,"error":error or "book_unparseable"})
        else: pbooks[market_id] = (book, received)
    for market_id, (body, received, error) in zip(kids, k_results):
        book = kalshi_top_book(body or {}) if body is not None else None
        if book is None: errors.append({"venue":"kalshi","market_id":market_id,"error":error or "book_unparseable"})
        else: kbooks[market_id] = (book, received)
    multipliers: dict[str, Decimal] = {}
    for series_ticker, (body, _received, error) in zip(series, s_results):
        meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
        fee_type = str(meta.get("fee_type") or ""); multiplier = _d(meta.get("fee_multiplier"))
        if fee_type in {"quadratic", "quadratic_with_maker_fees"} and multiplier is not None and D("0") <= multiplier <= D("100"):
            multipliers[series_ticker] = multiplier
        else: errors.append({"venue":"kalshi","market_id":series_ticker,"error":error or f"bad_fee_metadata:{fee_type}:{multiplier}"})
    priced: list[PricedQuote] = []; now = int(time.time() * 1000)
    for side in wanted:
        row = None
        if side.identity.venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None: row = make_priced_quote(side, book, received_ms=now, depth_source="event_prices_q1_probe_only")
            else: errors.append({"venue":"gemini","market_id":side.venue_market_id,"error":"buy_yes_no_unparseable"})
        elif side.identity.venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]; row = make_priced_quote(side, book, received_ms=received, depth_source="public_book_top_level")
        elif side.identity.venue == "kalshi" and side.venue_market_id in kbooks:
            book, received = kbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, kalshi_fee_multiplier=multipliers.get(ktoseries.get(side.venue_market_id, "")), depth_source="public_book_top_level")
        if row is not None: priced.append(row)
    return priced, errors, len(keys)


def _dedupe(opportunities: list[Any]) -> list[Any]:
    seen=set(); out=[]
    for opp in opportunities:
        legs=tuple(sorted(((opp.yes_venue,opp.yes_market_id),(opp.no_venue,opp.no_market_id)))); key=(legs,str(opp.unit_profit))
        if key not in seen: seen.add(key); out.append(opp)
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly_raw, kalshi = await asyncio.gather(fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        poly = full_game_only_events(poly_raw)
        raw_poly_markets = sum(len(e.get("markets") or []) for e in poly_raw); scoped_poly_markets = sum(len(e.get("markets") or []) for e in poly)
        priced, errors, key_count = await price_counterparts(http, gemini, poly, kalshi)
    locks = _dedupe(find_cross_venue_locks([row.quote for row in priced], max_quote_skew_ms=5000, min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"))
    result={"status":"THREE_VENUE_MLB_Q1_PRICE_SCAN","raw_events":{"gemini":len(gemini),"polymarket":len(poly_raw),"kalshi":len(kalshi)},
        "polymarket_scope":{"raw_markets":raw_poly_markets,"full_game_markets":scoped_poly_markets,"rejected_non_main_or_period":raw_poly_markets-scoped_poly_markets},
        "normal_event_counterpart_keys":key_count,"priced_quotes":len(priced),"priced_quotes_by_venue":dict(Counter(row.quote.venue for row in priced)),
        "one_contract_positive_locks":len(locks),"top_one_contract_locks":[row.as_dict() for row in locks[:50]],"fetch_error_count":len(errors),"fetch_errors":errors[:50],
        "pricing_scope":"Q1_TAKER_FEES_INCLUDED","gemini_depth_scope":"Q1_ONLY_UNTIL_WEBSOCKET_REVALIDATION","request_policy":"MAX4_CONCURRENT_429_BACKOFF",
        "rule_gate":"FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION","execution_eligible":False,"read_only":True,"order_entry":False}
    logger.info("THREE_VENUE_MLB_Q1_PRICE_SCAN %s", json.dumps(result,separators=(",",":"),default=str)); return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(run_once()),sort_keys=True,default=str))

if __name__ == "__main__": main()
