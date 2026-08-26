"""Dynamic read-only NFL Gemini<->Kalshi exact full-game total hot probe.

Discovers every active Gemini NFL full-contest OVER half-point total and joins it to
the exact Kalshi KXNFLTOTAL event/threshold independently of any combo. Then watches
actual public Gemini bookTicker and Kalshi depth with q=1, 100ms freshness, 50ms
receive skew, fee-aware economics, and a 50ms same-limit persistence recheck.

No order entry and no cross-venue rule certification. A >=3c survivor is an
ECONOMIC_CANDIDATE that must pass rule audit and final authenticated execution gates.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.gemini_sports_adapter import parse_team_event_ticker
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_hot_depth_probe import (
    GeminiNflBooks, kalshi_tob, best_candidate, same_limits,
)

D=Decimal
ONE=D("1")
KALSHI="https://external-api.kalshi.com/trade-api/v2"
ET=ZoneInfo("America/New_York")
logger=logging.getLogger("kalshi_arb.nfl_gk_dynamic_totals_probe")


@dataclass(frozen=True)
class Pair:
    key:str
    gemini:str
    kalshi:str
    event_title:str
    line:D


def _d(v:Any)->D|None:
    try:x=D(str(v))
    except Exception:return None
    return x if x.is_finite() else None


def _expected_k_event(parsed:Mapping[str,Any])->str:
    start=parsed["start"].astimezone(ET)
    return f"KXNFLTOTAL-{start:%y%b%d}".upper()+str(parsed["away"]).upper()+str(parsed["home"]).upper()


def _gemini_rows(events:list[dict[str,Any]])->list[tuple[str,str,D,str]]:
    out=[]
    for e in events:
        ticker=str(e.get("ticker") or e.get("eventTicker") or "")
        parsed=parse_team_event_ticker(ticker)
        if parsed is None or str(parsed.get("league") or "").upper()!="NFL" or str(parsed.get("code") or "").upper()!="T":
            continue
        k_event=_expected_k_event(parsed)
        title=str(e.get("title") or ticker)
        sm=e.get("sportsMarket") if isinstance(e.get("sportsMarket"),Mapping) else {}
        scope=sm.get("scope") if isinstance(sm.get("scope"),Mapping) else {}
        if sm and (str(sm.get("type") or "").lower()!="total" or str(scope.get("type") or "").lower() not in {"full_contest","full_game"}):
            continue
        for c in e.get("contracts") or []:
            if not isinstance(c,Mapping):continue
            strike=c.get("strike") if isinstance(c.get("strike"),Mapping) else {}
            if str(strike.get("type") or "").lower()!="over":continue
            line=_d(strike.get("value"))
            if line is None or (line*2)%2!=1:continue
            symbol=str(c.get("instrumentSymbol") or c.get("ticker") or c.get("contractTicker") or "")
            if symbol:out.append((symbol,k_event,line,title))
    return out


async def _kalshi_events(http:httpx.AsyncClient)->list[dict[str,Any]]:
    out=[];cursor=""
    for _ in range(8):
        params={"limit":200,"status":"open","series_ticker":"KXNFLTOTAL","with_nested_markets":"true"}
        if cursor:params["cursor"]=cursor
        r=await http.get(f"{KALSHI}/events",params=params);r.raise_for_status();body=r.json()
        out.extend(dict(x) for x in body.get("events",[]) if isinstance(x,Mapping))
        cursor=str(body.get("cursor") or "")
        if not cursor:break
        await asyncio.sleep(.05)
    return out


def _pairs(grows:list[tuple[str,str,D,str]],kevents:list[dict[str,Any]])->list[Pair]:
    kindex={str(e.get("event_ticker") or "").upper():e for e in kevents}
    out=[];seen=set()
    for symbol,ket,line,title in grows:
        event=kindex.get(ket)
        if not event:continue
        candidates=[]
        for m in event.get("markets") or []:
            if not isinstance(m,Mapping):continue
            if str(m.get("strike_type") or "").lower() not in {"greater","greater_than"}:continue
            if _d(m.get("floor_strike"))!=line:continue
            ticker=str(m.get("ticker") or "")
            if ticker:candidates.append(ticker)
        if len(candidates)!=1:continue
        marker=(symbol,candidates[0])
        if marker in seen:continue
        seen.add(marker);out.append(Pair(f"nfl_total|{ket}|{line}",symbol,candidates[0],title,line))
    return out


async def _next_update(g:GeminiNflBooks,k:EventingKalshiCrossBooks)->BookUpdate:
    gt=asyncio.create_task(g.updates.get());kt=asyncio.create_task(k.updates.get())
    done,pending=await asyncio.wait((gt,kt),return_when=asyncio.FIRST_COMPLETED)
    for t in pending:t.cancel()
    await asyncio.gather(*pending,return_exceptions=True)
    return next(iter(done)).result()


async def run(seconds:float)->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gevents,kevents=await asyncio.gather(fetch_active_sports_events(http),_kalshi_events(http))
    grows=_gemini_rows(gevents);pairs=_pairs(grows,kevents)
    logger.info("NFL_GK_DYNAMIC_TOTAL_DISCOVERY %s",json.dumps({"gemini_active_sports_events":len(gevents),"gemini_nfl_halfpoint_total_contracts":len(grows),"kalshi_nfl_total_events":len(kevents),"exact_counterparts":len(pairs),"read_only":True,"order_entry":False},separators=(",",":")))
    if not pairs:
        return {"status":"NO_EXACT_COUNTERPARTS","exact_counterparts":0,"order_entry":False}

    gem=GeminiNflBooks([p.gemini for p in pairs]);kal=EventingKalshiCrossBooks([p.kalshi for p in pairs])
    bymarker={}
    for i,p in enumerate(pairs):
        bymarker.setdefault(("gemini",p.gemini),[]).append(i);bymarker.setdefault(("kalshi",p.kalshi),[]).append(i)
    obs=[{"triggers":0,"both":0,"q1":0,"strict":0,"positive":0,"ge3":0,"rechecks":0,"survived":0,"max":D("-9")} for _ in pairs]
    pending={}

    async def recheck(i:int,direction:str,eg,ek,profit:D):
        obs[i]["rechecks"]+=1;await asyncio.sleep(.05);p=pairs[i]
        g2=gem.quote(p.gemini,max_age_ms=100);kq2=await kal.quote(p.kalshi,max_age_ms=100)
        if g2 is None or kq2 is None:return
        k2=kalshi_tob(kq2,p.kalshi)
        if abs(g2.ts_ms-k2.ts_ms)>50 or not same_limits(direction,eg,ek,g2,k2):return
        c2=best_candidate(g2,k2,max_skew_ms=50)
        if c2 is None or c2.direction!=direction or c2.locked_profit<profit:return
        obs[i]["survived"]+=1
        if c2.locked_profit>=D("0.03"):
            logger.info("NFL_GK_DYNAMIC_TOTAL_3C_SURVIVOR %s",json.dumps({"key":p.key,"event":p.event_title,"line":str(p.line),"gemini":p.gemini,"kalshi":p.kalshi,"direction":c2.direction,"locked_profit":str(c2.locked_profit),"quantity":str(c2.quantity),"classification":"ECONOMIC_CANDIDATE_RULES_UNCERTIFIED","order_entry":False},separators=(",",":")))

    tasks=[asyncio.create_task(gem.run()),asyncio.create_task(kal.run())]
    started=time.monotonic();deadline=started+max(10.0,seconds)
    try:
        while time.monotonic()<deadline:
            try:tr=await asyncio.wait_for(_next_update(gem,kal),timeout=1.0)
            except asyncio.TimeoutError:continue
            for i in bymarker.get((tr.venue,tr.market_id),[]):
                p=pairs[i];o=obs[i];o["triggers"]+=1
                g=gem.quote(p.gemini,max_age_ms=100);kq=await kal.quote(p.kalshi,max_age_ms=100)
                if g is None or kq is None:continue
                k=kalshi_tob(kq,p.kalshi);o["both"]+=1
                if min(g.yes_ask_size,g.no_ask_size,k.yes_ask_size,k.no_ask_size)>=ONE:o["q1"]+=1
                if abs(g.ts_ms-k.ts_ms)>50:continue
                o["strict"]+=1;c=best_candidate(g,k,max_skew_ms=50)
                if c is None:continue
                o["positive"]+=1;o["max"]=max(o["max"],c.locked_profit)
                if c.locked_profit>=D("0.03"):
                    o["ge3"]+=1
                    if i not in pending or pending[i].done():pending[i]=asyncio.create_task(recheck(i,c.direction,g,k,c.locked_profit))
    finally:
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if pending:await asyncio.gather(*pending.values(),return_exceptions=True)
        await kal.close()

    rows=[]
    for p,o in zip(pairs,obs):
        rows.append({"key":p.key,"event":p.event_title,"line":str(p.line),"gemini":p.gemini,"kalshi":p.kalshi,**{k:(str(v) if isinstance(v,D) else v) for k,v in o.items()}})
    rows.sort(key=lambda r:D(r["max"]),reverse=True)
    result={"status":"NFL_GK_DYNAMIC_TOTAL_SUMMARY","runtime_s":round(time.monotonic()-started,3),"live_single_leg_markets_scanned":len(grows)+sum(len(e.get("markets") or []) for e in kevents),"exact_counterparts_found":len(pairs),"pairs_with_q1_depth":sum(1 for o in obs if o["q1"]),"positive_pairs":sum(1 for o in obs if o["positive"]),"pairs_meeting_3c":sum(1 for o in obs if o["ge3"]),"three_cent_rechecks_started":sum(o["rechecks"] for o in obs),"three_cent_rechecks_survived":sum(o["survived"] for o in obs),"top_pairs":rows[:25],"classification":"SHADOW_ECONOMICS_RULES_UNCERTIFIED","realized_pnl":"0","order_entry":False}
    logger.info("NFL_GK_DYNAMIC_TOTAL_SUMMARY %s",json.dumps(result,separators=(",",":")))
    return result


def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--seconds",type=float,default=60.0);args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds))

if __name__=="__main__":main()
