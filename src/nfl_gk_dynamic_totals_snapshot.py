"""Public one-shot dynamic NFL Gemini<->Kalshi exact-total economics scan.

Starts from all active Gemini NFL full-game half-point total contracts, joins every
exact Kalshi KXNFLTOTAL threshold, uses current actual best bid/ask fields plus
Kalshi nested executable depth, and ranks q=1 fee-aware complement economics.
No authentication, rule certification, or order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
from typing import Any,Mapping

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_dynamic_totals_probe import _gemini_rows,_kalshi_events,_pairs
from src.nfl_gk_hot_depth_probe import nfl_kalshi_taker_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book

D=Decimal
ONE=D("1")
logger=logging.getLogger("kalshi_arb.nfl_gk_dynamic_totals_snapshot")


def _d(v:Any)->D|None:
    try:x=D(str(v))
    except Exception:return None
    return x if x.is_finite() else None


def _gbooks(events:list[dict[str,Any]])->dict[str,dict[str,D]]:
    out={}
    for e in events:
        for c in e.get("contracts") or []:
            if not isinstance(c,Mapping):continue
            symbol=str(c.get("instrumentSymbol") or c.get("ticker") or c.get("contractTicker") or "")
            prices=c.get("prices") if isinstance(c.get("prices"),Mapping) else {}
            ask=_d(prices.get("bestAsk"));bid=_d(prices.get("bestBid"))
            if symbol and ask is not None and bid is not None and D("0")<=bid<ask<=ONE:
                out[symbol]={"yes_ask":ask,"no_ask":ONE-bid}
    return out


def _kbooks(events:list[dict[str,Any]])->dict[str,Any]:
    out={}
    for e in events:
        for m in e.get("markets") or []:
            if isinstance(m,Mapping) and m.get("ticker"):
                out[str(m["ticker"])]=m
    return out


async def run_once()->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gevents,kevents=await asyncio.gather(fetch_active_sports_events(http),_kalshi_events(http))
    grows=_gemini_rows(gevents);pairs=_pairs(grows,kevents);gb=_gbooks(gevents);km=_kbooks(kevents)
    rows=[]
    for p in pairs:
        g=gb.get(p.gemini);k=kalshi_nested_top_book(km.get(p.kalshi,{}))
        if g is None or k is None:continue
        dirs=(
            ("GEMINI_OVER_KALSHI_UNDER",g["yes_ask"],k.no_ask,k.no_depth),
            ("KALSHI_OVER_GEMINI_UNDER",k.yes_ask,g["no_ask"],k.yes_depth),
        )
        for direction,g_or_k_over,other_under,kdepth in dirs:
            if kdepth<ONE:continue
            if direction.startswith("GEMINI"):
                gpx,kpx=g_or_k_over,other_under
            else:
                kpx,gpx=g_or_k_over,other_under
            gf=gemini_predictions_taker_fee(gpx,ONE);kf=nfl_kalshi_taker_fee(kpx,ONE)
            cost=gpx+kpx+gf+kf;profit=ONE-cost
            rows.append({"event":p.event_title,"line":str(p.line),"gemini":p.gemini,"kalshi":p.kalshi,"direction":direction,"gemini_price":str(gpx),"kalshi_price":str(kpx),"gemini_fee":str(gf),"kalshi_fee":str(kf),"combined_cost":str(cost),"locked_profit_if_rules_equivalent":str(profit),"kalshi_q1_depth":str(kdepth)})
    rows.sort(key=lambda r:D(r["locked_profit_if_rules_equivalent"]),reverse=True)
    below100=sum(1 for r in rows if D(r["gemini_price"])+D(r["kalshi_price"])<ONE)
    positive=sum(1 for r in rows if D(r["locked_profit_if_rules_equivalent"])>0)
    ge3=sum(1 for r in rows if D(r["locked_profit_if_rules_equivalent"])>=D("0.03"))
    result={"status":"NFL_GK_DYNAMIC_TOTAL_SNAPSHOT","live_single_leg_markets_scanned":len(grows)+sum(len(e.get("markets") or []) for e in kevents),"gemini_nfl_halfpoint_total_contracts":len(grows),"kalshi_nfl_total_markets":sum(len(e.get("markets") or []) for e in kevents),"exact_counterparts_found":len(pairs),"priced_directions":len(rows),"opportunities_raw_below_100c":below100,"opportunities_after_fee_positive":positive,"opportunities_meeting_3c":ge3,"top":rows[:30],"classification":"DISCOVERED_PRICED_RULES_UNCERTIFIED_GEMINI_DEPTH_REQUIRES_LIVE_RECHECK","order_entry":False,"realized_pnl":"0"}
    out=json.dumps(result,separators=(",",":"));logger.info("NFL_GK_DYNAMIC_TOTAL_SNAPSHOT %s",out);print("NFL_GK_DYNAMIC_TOTAL_SNAPSHOT "+out,flush=True);return result


def main()->None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())

if __name__=="__main__":main()
