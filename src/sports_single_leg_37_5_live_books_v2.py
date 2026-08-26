"""Targeted read-only SEA/TEN 37.5 live-book evaluator, v2.

Uses the league-specific Polymarket NFL endpoint and a deterministic Gemini NFL
T-total fallback because the generic Gemini adapter historically only had MLB
M/S/T ticker fallbacks. No order entry; cross-venue rules remain uncertified.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import json
import logging
import re
import time
from typing import Any, Mapping

import httpx

from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_hot_depth_probe import GeminiNflBooks
from src.strict_mlb_prop_snapshot import kalshi_top_book, poly_top_book

D=Decimal
ONE=D("1")
TARGET=D("37.5")
KALSHI="https://external-api.kalshi.com/trade-api/v2"
POLY="https://gateway.polymarket.us"
K_TICKER="KXNFLTOTAL-26AUG23SEATEN-38"
logger=logging.getLogger("kalshi_arb.sports_single_leg_37_5_live_books_v2")


def _d(v:Any)->D|None:
    try: x=D(str(v))
    except Exception: return None
    return x if x.is_finite() else None


def _ceil_cent(v:D)->D:
    return (v*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")


def _gem_fee(p:D)->D: return _ceil_cent(D("0.07")*p*(ONE-p))
def _kal_fee(p:D,m:D)->D: return _ceil_cent(D("0.07")*m*p*(ONE-p))
def _poly_fee(p:D,theta:D)->D: return (theta*p*(ONE-p)).quantize(D("0.01"),rounding=ROUND_HALF_EVEN)


def _event_is_seaten(obj:Mapping[str,Any])->bool:
    text=json.dumps(obj,sort_keys=True,default=str).lower()
    return ("seattle" in text or "seahawks" in text or "sea-ten" in text or "ten-sea" in text) and ("tennessee" in text or "titans" in text or "sea-ten" in text or "ten-sea" in text)


def _gemini_target(events:list[dict[str,Any]])->tuple[dict[str,Any]|None,dict[str,Any]|None]:
    # Deterministic event tickers already used by nfl_gk_hot_depth_probe:
    # GEMI-NFL-YYMMDDHHMM-AWAY-HOME-T, with total contracts beneath it.
    for e in events:
        ticker=str(e.get("ticker") or e.get("eventTicker") or "").upper()
        if "NFL-" not in ticker or not ("-SEA-TEN-" in ticker or "-TEN-SEA-" in ticker):
            continue
        if not ticker.endswith("-T"):
            continue
        for c in e.get("contracts") or []:
            if not isinstance(c,Mapping): continue
            strike=c.get("strike") if isinstance(c.get("strike"),Mapping) else {}
            st=str(strike.get("type") or "").lower()
            sv=_d(strike.get("value"))
            if sv!=TARGET or st not in {"over","over_or_equal"}:
                continue
            symbol=str(c.get("instrumentSymbol") or c.get("ticker") or c.get("contractTicker") or "")
            if symbol:
                return dict(e),dict(c)
    # Fallback for a structured NFL event whose ticker code is not T but whose
    # sportsMarket explicitly says full-contest total.
    for e in events:
        if not _event_is_seaten(e): continue
        sm=e.get("sportsMarket") if isinstance(e.get("sportsMarket"),Mapping) else {}
        if str(sm.get("type") or "").lower()!="total": continue
        scope=sm.get("scope") if isinstance(sm.get("scope"),Mapping) else {}
        if str(scope.get("type") or "").lower() not in {"full_contest","full_game"}: continue
        for c in e.get("contracts") or []:
            if not isinstance(c,Mapping): continue
            strike=c.get("strike") if isinstance(c.get("strike"),Mapping) else {}
            if _d(strike.get("value"))==TARGET and str(strike.get("type") or "").lower() in {"over","over_or_equal"}:
                return dict(e),dict(c)
    return None,None


def _poly_target(events:list[dict[str,Any]])->tuple[dict[str,Any]|None,dict[str,Any]|None]:
    for e in events:
        if not _event_is_seaten(e): continue
        for m in e.get("markets") or []:
            if not isinstance(m,Mapping): continue
            if _d(m.get("line"))!=TARGET: continue
            if str(m.get("marketType") or "").lower()!="totals": continue
            typ=str(m.get("sportsMarketType") or "").lower()
            if typ!="football_team_full_game_total": continue
            return dict(e),dict(m)
    return None,None


def _compact(row:Mapping[str,Any]|None)->dict[str,Any]|None:
    if row is None:return None
    keep=("id","ticker","instrumentSymbol","slug","question","title","description","rules","rulesPrimary","rulesSecondary","rules_primary","rules_secondary","sportsMarketType","marketType","line","gameStartTime","startTime","endDate","minimumTradeQty","feeCoefficient","sportsMarket","strike","label","abbreviatedName","prices")
    return {k:row.get(k) for k in keep if k in row}


async def _poly_nfl(http:httpx.AsyncClient)->list[dict[str,Any]]:
    r=await http.get(f"{POLY}/v2/leagues/nfl/events",params={"limit":1000,"active":"true","closed":"false"})
    r.raise_for_status()
    body=r.json()
    return [dict(x) for x in body.get("events",[]) if isinstance(x,Mapping)]


async def _gem_live(symbol:str,seconds:float=6.0):
    store=GeminiNflBooks([symbol]); task=asyncio.create_task(store.run()); end=time.monotonic()+seconds
    try:
        while time.monotonic()<end:
            q=store.quote(symbol,max_age_ms=1000)
            if q is not None:return q
            await asyncio.sleep(.02)
        return None
    finally:
        task.cancel(); await asyncio.gather(task,return_exceptions=True)


async def run_once()->dict[str,Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        gevents,pevents=await asyncio.gather(fetch_active_sports_events(http),_poly_nfl(http))
        gevent,gcontract=_gemini_target(gevents)
        pevent,pmarket=_poly_target(pevents)
        symbol=str((gcontract or {}).get("instrumentSymbol") or (gcontract or {}).get("ticker") or (gcontract or {}).get("contractTicker") or "")
        reqs=[
            http.get(f"{KALSHI}/markets/{K_TICKER}"),
            http.get(f"{KALSHI}/markets/{K_TICKER}/orderbook",params={"depth":1}),
            http.get(f"{KALSHI}/series/KXNFLTOTAL"),
        ]
        if pmarket and pmarket.get("id") is not None:
            reqs.append(http.get(f"{POLY}/v1/markets/{pmarket['id']}/book"))
        rr=await asyncio.gather(*reqs,return_exceptions=True)

    km_resp,kb_resp,ks_resp=rr[:3]
    km=km_resp.json().get("market",{}) if isinstance(km_resp,httpx.Response) and km_resp.status_code==200 else {}
    kb=kalshi_top_book(kb_resp.json()) if isinstance(kb_resp,httpx.Response) and kb_resp.status_code==200 else None
    ks=ks_resp.json().get("series",{}) if isinstance(ks_resp,httpx.Response) and ks_resp.status_code==200 else {}
    mult=_d(ks.get("fee_multiplier")) or D("1")
    pb=poly_top_book(rr[3].json()) if len(rr)>3 and isinstance(rr[3],httpx.Response) and rr[3].status_code==200 else None
    theta=max(D("0.05"),_d((pmarket or {}).get("feeCoefficient")) or D("0.05"))
    gb=await _gem_live(symbol) if symbol else None

    venues:dict[str,dict[str,Any]]={}
    if gb is not None:
        venues["gemini"]={"market_id":symbol,"yes_ask":gb.yes_ask,"yes_depth":gb.yes_ask_size,"no_ask":ONE-gb.yes_bid,"no_depth":gb.yes_bid_size,"ts_ms":gb.ts_ms}
    if pb is not None and pmarket:
        venues["polymarket"]={"market_id":str(pmarket.get("id")),"slug":pmarket.get("slug"),"yes_ask":pb.yes_ask,"yes_depth":pb.yes_depth,"no_ask":pb.no_ask,"no_depth":pb.no_depth,"fee_theta":theta}
    if kb is not None:
        venues["kalshi"]={"market_id":K_TICKER,"yes_ask":kb.yes_ask,"yes_depth":kb.yes_depth,"no_ask":kb.no_ask,"no_depth":kb.no_depth,"fee_multiplier":mult}

    def fee(v:str,p:D)->D:
        return _gem_fee(p) if v=="gemini" else _poly_fee(p,theta) if v=="polymarket" else _kal_fee(p,mult)

    plans=[]
    for yv,a in venues.items():
        for nv,b in venues.items():
            if yv==nv:continue
            if D(str(a["yes_depth"]))<ONE or D(str(b["no_depth"]))<ONE:continue
            yp,np=D(str(a["yes_ask"])),D(str(b["no_ask"])); yf,nf=fee(yv,yp),fee(nv,np); cost=yp+np+yf+nf
            plans.append({"buy_over_venue":yv,"buy_over_market":a["market_id"],"buy_over_price":str(yp),"buy_over_fee":str(yf),"buy_under_venue":nv,"buy_under_market":b["market_id"],"buy_under_price":str(np),"buy_under_fee":str(nf),"combined_cost":str(cost),"locked_profit_if_rules_equivalent":str(ONE-cost),"q1_depth":str(min(D(str(a["yes_depth"])),D(str(b["no_depth"]))))})
    plans.sort(key=lambda x:D(x["locked_profit_if_rules_equivalent"]),reverse=True)
    payload={
        "target":"NFL SEA-TEN full_game total_points 37.5",
        "classification":"DISCOVERED_PRICED_NOT_RULE_CERTIFIED",
        "gemini_event_count":len(gevents),"poly_nfl_event_count":len(pevents),
        "gemini_found":bool(gcontract),"polymarket_found":bool(pmarket),"kalshi_found":bool(km),
        "gemini_event":_compact(gevent),"gemini_contract":_compact(gcontract),"polymarket_market":_compact(pmarket),"kalshi_market":_compact(km),
        "venues":{k:{kk:(str(vv) if isinstance(vv,D) else vv) for kk,vv in v.items()} for k,v in venues.items()},
        "best_plan":plans[0] if plans else None,"all_plans":plans,
        "meets_3c_economic_gate":bool(plans and D(plans[0]["locked_profit_if_rules_equivalent"])>=D("0.03")),
        "order_entry":False,
    }
    out=json.dumps(payload,separators=(",",":"),sort_keys=True,default=str)
    logger.info("SPORTS_SINGLE_LEG_37_5_V2 %s",out); print("SPORTS_SINGLE_LEG_37_5_V2 "+out,flush=True)
    return payload


def main()->None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())

if __name__=="__main__":main()
