"""Read-only q=1 MLB lock scanner across Gemini, Polymarket US, and Kalshi."""
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
from src.strict_mlb_prop_snapshot import KALSHI, POLY, kalshi_top_book, poly_top_book
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

D = Decimal
logger = logging.getLogger("kalshi_arb.three_venue_mlb_price_scan")
START_TOLERANCE_MS = 5 * 60 * 1000


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _eligible_rows(rows: list[NormalizedSide]) -> tuple[list[NormalizedSide], int]:
    """Return counterpart rows grouped by contract semantics AND physical start.

    ``contract_key`` intentionally excludes scheduled start. MLB teams frequently
    play on consecutive days, so grouping an entire matchup by contract_key and
    rejecting it when max(start)-min(start)>5m incorrectly drops every moneyline
    for repeated series. Cluster each semantic key into physical-game start windows
    first, then require >=2 venues inside that window.
    """
    groups: dict[str, list[NormalizedSide]] = defaultdict(list)
    for row in rows:
        groups[row.contract_key].append(row)

    eligible_ids: set[int] = set()
    eligible_clusters = 0
    for matches in groups.values():
        pending = sorted(matches, key=lambda r: r.identity.event.scheduled_start_ms)
        while pending:
            anchor = pending[0].identity.event.scheduled_start_ms
            cluster = [r for r in pending if r.identity.event.scheduled_start_ms - anchor <= START_TOLERANCE_MS]
            cluster_ids = {id(r) for r in cluster}
            pending = [r for r in pending if id(r) not in cluster_ids]
            if len({r.identity.venue for r in cluster}) < 2:
                continue
            eligible_clusters += 1
            eligible_ids.update(cluster_ids)
    return [row for row in rows if id(row) in eligible_ids], eligible_clusters


def _eligible_keys(rows: list[NormalizedSide]) -> set[str]:
    """Compatibility helper: semantic keys that have at least one valid start cluster."""
    eligible, _ = _eligible_rows(rows)
    return {row.contract_key for row in eligible}


def _gemini_contracts(events: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in events:
        for contract in event.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or "")
            if symbol:
                out[symbol] = contract
    return out


def _kalshi_series(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for event in events:
        series = str(event.get("series_ticker") or "")
        for market in event.get("markets") or []:
            if isinstance(market, Mapping) and market.get("ticker"):
                out[str(market["ticker"])] = series
    return out


async def _get(http: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str, **kwargs: Any) -> tuple[dict[str, Any] | None, int, str | None]:
    async with semaphore:
        try:
            response = await http.get(url, **kwargs)
            received = int(time.time() * 1000)
            response.raise_for_status()
            body = response.json()
            return (body if isinstance(body, dict) else {}), received, None
        except Exception as exc:
            return None, int(time.time() * 1000), f"{type(exc).__name__}:{str(exc)[:180]}"


async def price_counterparts(http: httpx.AsyncClient, gemini_events: list[dict[str, Any]], poly_events: list[dict[str, Any]], kalshi_events: list[dict[str, Any]]) -> tuple[list[PricedQuote], list[dict[str, str]], int]:
    rows = normalize_gemini_mlb(gemini_events) + normalize_polymarket_mlb(poly_events) + normalize_kalshi_mlb_eastern(kalshi_events)
    wanted, cluster_count = _eligible_rows(rows)
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
    pbooks = {}
    for market_id, (body, received, error) in zip(pids, p_results):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None: errors.append({"venue":"polymarket","market_id":market_id,"error":error or "book_unparseable"})
        else: pbooks[market_id] = (book, received)
    kbooks = {}
    for market_id, (body, received, error) in zip(kids, k_results):
        book = kalshi_top_book(body or {}) if body is not None else None
        if book is None: errors.append({"venue":"kalshi","market_id":market_id,"error":error or "book_unparseable"})
        else: kbooks[market_id] = (book, received)
    multipliers: dict[str, Decimal] = {}
    for series_ticker, (body, _received, error) in zip(series, s_results):
        meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = _d(meta.get("fee_multiplier"))
        if fee_type in {"quadratic", "quadratic_with_maker_fees"} and multiplier is not None and D("0") <= multiplier <= D("100"):
            multipliers[series_ticker] = multiplier
        else:
            errors.append({"venue":"kalshi","market_id":series_ticker,"error":error or f"bad_fee_metadata:{fee_type}:{multiplier}"})
    priced: list[PricedQuote] = []
    now = int(time.time() * 1000)
    for side in wanted:
        row = None
        if side.identity.venue == "gemini":
            book = gemini_contract_book(gcontracts.get(side.venue_market_id, {}))
            if book is not None: row = make_priced_quote(side, book, received_ms=now, depth_source="event_prices_q1_probe_only")
            else: errors.append({"venue":"gemini","market_id":side.venue_market_id,"error":"buy_yes_no_unparseable"})
        elif side.identity.venue == "polymarket" and side.venue_market_id in pbooks:
            book, received = pbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, depth_source="public_book_top_level")
        elif side.identity.venue == "kalshi" and side.venue_market_id in kbooks:
            book, received = kbooks[side.venue_market_id]
            row = make_priced_quote(side, book, received_ms=received, kalshi_fee_multiplier=multipliers.get(ktoseries.get(side.venue_market_id, "")), depth_source="public_book_top_level")
        if row is not None: priced.append(row)
    return priced, errors, cluster_count


def _dedupe(opportunities: list[Any]) -> list[Any]:
    seen=set(); out=[]
    for opp in opportunities:
        legs=tuple(sorted(((opp.yes_venue,opp.yes_market_id),(opp.no_venue,opp.no_market_id))))
        key=(legs,str(opp.unit_profit))
        if key not in seen: seen.add(key); out.append(opp)
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly, kalshi = await asyncio.gather(fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        priced, errors, key_count = await price_counterparts(http, gemini, poly, kalshi)
    locks = _dedupe(find_cross_venue_locks([row.quote for row in priced], max_quote_skew_ms=5000, min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"))
    result={
        "status":"THREE_VENUE_MLB_Q1_PRICE_SCAN","raw_events":{"gemini":len(gemini),"polymarket":len(poly),"kalshi":len(kalshi)},
        "normal_event_counterpart_start_clusters":key_count,"priced_quotes":len(priced),"priced_quotes_by_venue":dict(Counter(row.quote.venue for row in priced)),
        "one_contract_positive_locks":len(locks),"top_one_contract_locks":[row.as_dict() for row in locks[:50]],
        "fetch_error_count":len(errors),"fetch_errors":errors[:50],"pricing_scope":"Q1_TAKER_FEES_INCLUDED",
        "gemini_depth_scope":"Q1_ONLY_UNTIL_WEBSOCKET_REVALIDATION","rule_gate":"FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible":False,"read_only":True,"order_entry":False,
    }
    logger.info("THREE_VENUE_MLB_Q1_PRICE_SCAN %s", json.dumps(result,separators=(",",":"),default=str))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(run_once()),sort_keys=True,default=str))

if __name__ == "__main__": main()
