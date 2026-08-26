"""Read-only live-book probe for SEA/TEN full-game total 37.5.

Discovers the exact current 37.5 full-game total independently on Gemini,
Polymarket US, and Kalshi, then prices all cross-venue exact-complement directions
at q=1 using current marketable top-of-book quotes. This module does NOT certify
cross-venue settlement equivalence and contains no order-entry code.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import json
import logging
import time
from typing import Any, Mapping

import httpx

from src.gemini_sports_adapter import normalize_events
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_hot_depth_probe import GeminiNflBooks
from src.sports_kp_live_event_inventory import fetch_current_sports_events
from src.strict_mlb_prop_snapshot import kalshi_top_book, poly_top_book

D = Decimal
ONE = D("1")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY = "https://gateway.polymarket.us"
TARGET = D("37.5")
K_TICKER = "KXNFLTOTAL-26AUG23SEATEN-38"
logger = logging.getLogger("kalshi_arb.sports_single_leg_37_5_live_books")


def _d(v: Any) -> D | None:
    try:
        x = D(str(v))
    except Exception:
        return None
    return x if x.is_finite() else None


def _ceil_cent(v: D) -> D:
    return (v * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def gemini_fee(price: D) -> D:
    return _ceil_cent(D("0.07") * price * (ONE - price))


def kalshi_fee(price: D, multiplier: D) -> D:
    # Conservative whole-cent upper bound used by existing sports execution research.
    return _ceil_cent(D("0.07") * multiplier * price * (ONE - price))


def polymarket_fee(price: D, theta: D) -> D:
    # Polymarket US sports payload exposes feeCoefficient; use it when present and
    # conservatively round to cents, never below the existing 5% model.
    raw = theta * price * (ONE - price)
    return raw.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def _is_sea_ten_event(row: Mapping[str, Any]) -> bool:
    text = json.dumps(row, sort_keys=True, default=str).lower()
    return ("seattle" in text or "seahawks" in text or "sea-ten" in text) and (
        "tennessee" in text or "titans" in text or "sea-ten" in text
    )


def _poly_37(events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for event in events:
        if not _is_sea_ten_event(event):
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            line = _d(market.get("line"))
            if line != TARGET:
                continue
            if str(market.get("sportsMarketType") or "") != "football_team_full_game_total":
                continue
            return dict(event), dict(market)
    return None, None


def _gemini_37(events: list[dict[str, Any]]) -> tuple[Any | None, dict[str, Any] | None, dict[str, Any] | None]:
    rows = normalize_events(events)
    for row in rows:
        ident = row.identity
        if ident.event.league != "nfl" or ident.market_family != "game_total" or ident.scope != "full_contest":
            continue
        if tuple(ident.event.participants) != ("SEA", "TEN") or ident.line != TARGET:
            continue
        if ident.comparator != "GT":
            continue
        # Find raw event/contract for fee/rule metadata.
        for event in events:
            for contract in event.get("contracts") or []:
                if isinstance(contract, Mapping) and str(contract.get("instrumentSymbol") or contract.get("ticker") or "") == row.instrument_symbol:
                    return row, dict(event), dict(contract)
        return row, None, None
    return None, None, None


def _compact_rules(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keep = (
        "ticker","instrumentSymbol","question","title","description","rules","rulesPrimary","rulesSecondary",
        "rules_primary","rules_secondary","sportsMarketType","sportsMarketTypeV2","marketType","line",
        "gameStartTime","startTime","endDate","minimumTradeQty","feeCoefficient","settlementSource",
        "resultSource","sportsMarket","strike","label","abbreviatedName",
    )
    return {k: row.get(k) for k in keep if k in row}


async def _live_gemini(symbol: str, seconds: float = 5.0):
    books = GeminiNflBooks([symbol])
    task = asyncio.create_task(books.run())
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            quote = books.quote(symbol, max_age_ms=1000)
            if quote is not None:
                return quote
            await asyncio.sleep(0.025)
        return None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gemini_events, poly_events = await asyncio.gather(
            fetch_active_sports_events(http),
            fetch_current_sports_events(http, horizon_hours=48),
        )
        grow, gevent, gcontract = _gemini_37(gemini_events)
        pevent, pmarket = _poly_37(poly_events)

        requests = [
            http.get(f"{KALSHI}/markets/{K_TICKER}"),
            http.get(f"{KALSHI}/markets/{K_TICKER}/orderbook", params={"depth":1}),
            http.get(f"{KALSHI}/series/KXNFLTOTAL"),
        ]
        if pmarket and pmarket.get("id"):
            requests.append(http.get(f"{POLY}/v1/markets/{pmarket['id']}/book"))
        responses = await asyncio.gather(*requests, return_exceptions=True)

    k_meta_resp, k_book_resp, k_series_resp = responses[:3]
    k_meta = k_meta_resp.json().get("market", {}) if isinstance(k_meta_resp, httpx.Response) and k_meta_resp.status_code == 200 else {}
    k_book_payload = k_book_resp.json() if isinstance(k_book_resp, httpx.Response) and k_book_resp.status_code == 200 else {}
    k_series = k_series_resp.json().get("series", {}) if isinstance(k_series_resp, httpx.Response) and k_series_resp.status_code == 200 else {}
    kbook = kalshi_top_book(k_book_payload)
    kmult = _d(k_series.get("fee_multiplier")) or D("1")

    pbook = None
    if len(responses) > 3 and isinstance(responses[3], httpx.Response) and responses[3].status_code == 200:
        pbook = poly_top_book(responses[3].json())
    ptheta = max(D("0.05"), _d((pmarket or {}).get("feeCoefficient")) or D("0.05"))

    gbook = await _live_gemini(grow.instrument_symbol) if grow is not None else None

    venues: dict[str, dict[str, Any]] = {}
    if gbook is not None:
        venues["gemini"] = {
            "market_id":grow.instrument_symbol,
            "yes_ask":gbook.yes_ask,"yes_depth":gbook.yes_ask_size,
            "no_ask":ONE-gbook.yes_bid,"no_depth":gbook.yes_bid_size,
            "ts_ms":gbook.ts_ms,
        }
    if pbook is not None and pmarket is not None:
        venues["polymarket"] = {
            "market_id":str(pmarket.get("id")),"slug":pmarket.get("slug"),
            "yes_ask":pbook.yes_ask,"yes_depth":pbook.yes_depth,
            "no_ask":pbook.no_ask,"no_depth":pbook.no_depth,
            "ts_ms":int(time.time()*1000),"fee_theta":ptheta,
        }
    if kbook is not None:
        venues["kalshi"] = {
            "market_id":K_TICKER,"yes_ask":kbook.yes_ask,"yes_depth":kbook.yes_depth,
            "no_ask":kbook.no_ask,"no_depth":kbook.no_depth,
            "ts_ms":int(time.time()*1000),"fee_multiplier":kmult,
        }

    def fee(venue: str, px: D) -> D:
        if venue == "gemini": return gemini_fee(px)
        if venue == "polymarket": return polymarket_fee(px, ptheta)
        if venue == "kalshi": return kalshi_fee(px, kmult)
        raise ValueError(venue)

    plans: list[dict[str, Any]] = []
    names = sorted(venues)
    for yes_v in names:
        for no_v in names:
            if yes_v == no_v:
                continue
            a, b = venues[yes_v], venues[no_v]
            if D(str(a["yes_depth"])) < ONE or D(str(b["no_depth"])) < ONE:
                continue
            ypx, npx = D(str(a["yes_ask"])), D(str(b["no_ask"]))
            yf, nf = fee(yes_v, ypx), fee(no_v, npx)
            cost = ypx+npx+yf+nf
            plans.append({
                "buy_yes_venue":yes_v,"buy_yes_market":a["market_id"],"buy_yes_price":str(ypx),"buy_yes_fee":str(yf),
                "buy_no_venue":no_v,"buy_no_market":b["market_id"],"buy_no_price":str(npx),"buy_no_fee":str(nf),
                "combined_cost":str(cost),"locked_profit_if_rules_equivalent":str(ONE-cost),
                "q1_depth":str(min(D(str(a["yes_depth"])),D(str(b["no_depth"])))),
            })
    plans.sort(key=lambda x:D(x["locked_profit_if_rules_equivalent"]), reverse=True)

    payload = {
        "target":"NFL SEA-TEN full_game total_points 37.5",
        "classification":"DISCOVERED_PRICED_NOT_RULE_CERTIFIED",
        "gemini_found":grow is not None,"polymarket_found":pmarket is not None,"kalshi_found":bool(k_meta),
        "gemini_rules":_compact_rules(gevent),"gemini_contract":_compact_rules(gcontract),
        "polymarket_rules":_compact_rules(pmarket),"kalshi_rules":_compact_rules(k_meta),
        "venues":{k:{kk:(str(vv) if isinstance(vv,D) else vv) for kk,vv in v.items()} for k,v in venues.items()},
        "best_plan":plans[0] if plans else None,"all_plans":plans,
        "meets_3c_economic_gate":bool(plans and D(plans[0]["locked_profit_if_rules_equivalent"]) >= D("0.03")),
        "order_entry":False,
    }
    line = json.dumps(payload,separators=(",",":"),sort_keys=True,default=str)
    logger.info("SPORTS_SINGLE_LEG_37_5_LIVE_BOOKS %s",line)
    print("SPORTS_SINGLE_LEG_37_5_LIVE_BOOKS "+line,flush=True)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())

if __name__ == "__main__":
    main()
