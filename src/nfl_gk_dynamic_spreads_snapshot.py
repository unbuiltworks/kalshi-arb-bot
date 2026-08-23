"""Public one-shot NFL Gemini<->Kalshi exact spread economics scan.

Starts from every active Gemini NFL full-contest spread contract normalized as
TEAM_MARGIN > X, joins it to the exact Kalshi KXNFLSPREAD event/team/threshold,
and prices both q=1 complementary directions using actual marketable prices.

No authentication, rule certification, or order entry. Positive economics remain
DISCOVERED until exact rule/freshness/depth/persistence/fill-granularity gates pass.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import re
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_sports_adapter import normalize_events, parse_team_event_ticker
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_hot_depth_probe import nfl_kalshi_taker_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book

D = Decimal
ONE = D("1")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
logger = logging.getLogger("kalshi_arb.nfl_gk_dynamic_spreads_snapshot")


def _d(v: Any) -> D | None:
    try:
        x = D(str(v))
    except Exception:
        return None
    return x if x.is_finite() else None


def _event_parts(symbol: str) -> tuple[str, str, str] | None:
    # GEMI-NFL-YYMMDDHHMM-AWAY-HOME-S-...
    m = re.match(r"^GEMI-NFL-(\d{10})-([A-Z]{2,4})-([A-Z]{2,4})-S-", symbol.upper())
    return m.groups() if m else None


def _month(mon: str) -> int:
    return {x:i+1 for i,x in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"))}[mon]


def _kalshi_event_for_symbol(symbol: str) -> str | None:
    p = _event_parts(symbol)
    if p is None:
        return None
    stamp, away, home = p
    yy, mm, dd = stamp[:2], stamp[2:4], stamp[4:6]
    months = ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")
    mon = months[int(mm)-1]
    return f"KXNFLSPREAD-{yy}{mon}{int(dd):02d}{away}{home}".upper()


async def _kalshi_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(8):
        params: dict[str, Any] = {"limit":200,"status":"open","series_ticker":"KXNFLSPREAD","with_nested_markets":"true"}
        if cursor:
            params["cursor"] = cursor
        r = await http.get(f"{KALSHI}/events", params=params)
        r.raise_for_status()
        body = r.json()
        out.extend(dict(x) for x in body.get("events",[]) if isinstance(x, Mapping))
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
        await asyncio.sleep(.05)
    return out


def _gemini_spreads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contracts: dict[str, Mapping[str, Any]] = {}
    for e in events:
        for c in e.get("contracts") or []:
            if isinstance(c, Mapping):
                s = str(c.get("instrumentSymbol") or c.get("ticker") or c.get("contractTicker") or "")
                if s:
                    contracts[s] = c
    out = []
    for row in normalize_events(events):
        i = row.identity
        if i.event.league != "nfl" or i.market_family != "spread" or i.scope != "full_contest":
            continue
        if i.comparator != "GT" or i.line is None:
            continue
        # Kalshi's spread family encodes win-margin thresholds. Negative-margin
        # propositions are not assumed equivalent; they require a separate transform.
        if i.line < 0:
            continue
        c = contracts.get(row.instrument_symbol) or {}
        prices = c.get("prices") if isinstance(c.get("prices"), Mapping) else {}
        ask = _d(prices.get("bestAsk")); bid = _d(prices.get("bestBid"))
        if ask is None or bid is None or not (D("0") <= bid < ask <= ONE):
            continue
        ket = _kalshi_event_for_symbol(row.instrument_symbol)
        if ket is None:
            continue
        out.append({
            "symbol": row.instrument_symbol,
            "event_key": ket,
            "subject": i.subject.upper(),
            "line": i.line,
            "yes_ask": ask,
            "no_ask": ONE-bid,
            "label": row.contract_label,
            "participants": list(i.event.participants),
        })
    return out


def _kalshi_market_subject(event_ticker: str, market: Mapping[str, Any]) -> str | None:
    ticker = str(market.get("ticker") or "").upper()
    if not ticker.startswith(event_ticker.upper()+"-"):
        return None
    suffix = ticker[len(event_ticker)+1:]
    m = re.match(r"^([A-Z]{2,4})\d+$", suffix)
    return m.group(1) if m else None


def _kalshi_index(events: list[dict[str, Any]]) -> dict[tuple[str,str,D], list[Mapping[str, Any]]]:
    out: dict[tuple[str,str,D], list[Mapping[str, Any]]] = {}
    for e in events:
        et = str(e.get("event_ticker") or "").upper()
        for m in e.get("markets") or []:
            if not isinstance(m, Mapping):
                continue
            line = _d(m.get("floor_strike"))
            subject = _kalshi_market_subject(et, m)
            st = str(m.get("strike_type") or "").lower()
            if line is None or subject is None or st not in {"greater","greater_than"}:
                continue
            out.setdefault((et,subject,line),[]).append(m)
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gevents, kevents = await asyncio.gather(fetch_active_sports_events(http), _kalshi_events(http))
    grows = _gemini_spreads(gevents)
    kidx = _kalshi_index(kevents)
    rows = []
    exact = 0
    for g in grows:
        matches = kidx.get((g["event_key"],g["subject"],g["line"]),[])
        if len(matches) != 1:
            continue
        exact += 1
        km = matches[0]
        kb = kalshi_nested_top_book(km)
        if kb is None:
            continue
        ticker = str(km.get("ticker") or "")
        directions = (
            ("GEMINI_YES_KALSHI_NO",g["yes_ask"],kb.no_ask,kb.no_depth),
            ("KALSHI_YES_GEMINI_NO",kb.yes_ask,g["no_ask"],kb.yes_depth),
        )
        for direction, first, second, kdepth in directions:
            if kdepth < ONE:
                continue
            if direction.startswith("GEMINI"):
                gp, kp = first, second
            else:
                kp, gp = first, second
            gf = gemini_predictions_taker_fee(gp, ONE)
            kf = nfl_kalshi_taker_fee(kp, ONE)
            cost = gp + kp + gf + kf
            rows.append({
                "event_key":g["event_key"],"team":g["subject"],"line":str(g["line"]),
                "gemini":g["symbol"],"kalshi":ticker,"direction":direction,
                "gemini_price":str(gp),"kalshi_price":str(kp),"gemini_fee":str(gf),"kalshi_fee":str(kf),
                "raw_cost":str(gp+kp),"combined_cost":str(cost),
                "locked_profit_if_rules_equivalent":str(ONE-cost),"kalshi_q1_depth":str(kdepth),
            })
    rows.sort(key=lambda r:D(r["locked_profit_if_rules_equivalent"]), reverse=True)
    result = {
        "status":"NFL_GK_DYNAMIC_SPREAD_SNAPSHOT",
        "gemini_active_sports_events":len(gevents),
        "gemini_positive_margin_spread_contracts":len(grows),
        "kalshi_spread_events":len(kevents),
        "kalshi_spread_markets":sum(len(e.get("markets") or []) for e in kevents),
        "exact_counterparts_found":exact,
        "priced_directions":len(rows),
        "opportunities_raw_below_100c":sum(1 for r in rows if D(r["raw_cost"])<ONE),
        "opportunities_after_fee_positive":sum(1 for r in rows if D(r["locked_profit_if_rules_equivalent"])>0),
        "opportunities_meeting_3c":sum(1 for r in rows if D(r["locked_profit_if_rules_equivalent"])>=D("0.03")),
        "top":rows[:50],
        "classification":"DISCOVERED_PRICED_RULES_UNCERTIFIED_GEMINI_DEPTH_REQUIRES_LIVE_RECHECK",
        "order_entry":False,"realized_pnl":"0",
    }
    out=json.dumps(result,separators=(",",":"));logger.info("NFL_GK_DYNAMIC_SPREAD_SNAPSHOT %s",out);print("NFL_GK_DYNAMIC_SPREAD_SNAPSHOT "+out,flush=True)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())

if __name__=="__main__":main()
