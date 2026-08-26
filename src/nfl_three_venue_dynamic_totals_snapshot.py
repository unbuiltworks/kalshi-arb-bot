"""Dynamic read-only NFL full-game total exact-complement scan across G/P/K.

Discovers all active Gemini NFL full-contest half-point totals, Polymarket US NFL
full-game total markets, and Kalshi KXNFLTOTAL markets. Groups only exact event/date/
threshold identities and prices every two-venue OVER+UNDER construction at q=1.
Nested best quotes are discovery-only; any >=3c result still requires true final
book depth, rule certification, freshness/skew/persistence, and authenticated entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal,ROUND_CEILING,ROUND_HALF_EVEN
import json
import logging
import re
from typing import Any,Mapping

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_dynamic_totals_probe import _gemini_rows,_kalshi_events,_pairs
from src.nfl_gk_hot_depth_probe import nfl_kalshi_taker_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book

D=Decimal;ONE=D("1")
POLY="https://gateway.polymarket.us"
logger=logging.getLogger("kalshi_arb.nfl_three_venue_dynamic_totals_snapshot")
P_RE=re.compile(r"^tsc-nfl-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-total-",re.I)


def _d(v:Any)->D|None:
    try:x=D(str(v))
    except Exception:return None
    return x if x.is_finite() else None


def _poly_fee(p:D,theta:D)->D:
    return (theta*p*(ONE-p)).quantize(D("0.01"),rounding=ROUND_HALF_EVEN)


def _gcontracts(events:list[dict[str,Any]])->dict[str,Mapping[str,Any]]:
    out={}
    for e in events:
        for c in e.get("contracts") or []:
            if isinstance(c,Mapping):
                s=str(c.get("instrumentSymbol") or c.get("ticker") or c.get("contractTicker") or "")
                if s:out[s]=c
    return out


def _kmarkets(events:list[dict[str,Any]])->dict[str,Mapping[str,Any]]:
    return {str(m["ticker"]):m for e in events for m in (e.get("markets") or []) if isinstance(m,Mapping) and m.get("ticker")}


async def _poly_events(http:httpx.AsyncClient)->list[dict[str,Any]]:
    r=await http.get(f"{POLY}/v2/leagues/nfl/events",params={"limit":1000,"active":"true","closed":"false"});r.raise_for_status()
    return [dict(x) for x in r.json().get("events",[]) if isinstance(x,Mapping)]


def _poly_rows(events:list[dict[str,Any]])->list[dict[str,Any]]:
    out=[]
    for e in events:
        for m in e.get("markets") or []:
            if not isinstance(m,Mapping):continue
            if str(m.get("marketType") or "").lower()!="totals" or str(m.get("sportsMarketType") or "").lower()!="football_team_full_game_total":continue
            line=_d(m.get("line"));slug=str(m.get("slug") or "");match=P_RE.match(slug)
            if line is None or (line*2)%2!=1 or not match:continue
            a,b,date=match.groups();ask=_d(((m.get("bestAskQuote") or {}).get("value") if isinstance(m.get("bestAskQuote"),Mapping) else m.get("bestAsk")))
            bid=_d(((m.get("bestBidQuote") or {}).get("value") if isinstance(m.get("bestBidQuote"),Mapping) else m.get("bestBid")))
            # Market-side quotes can be present when aggregate best fields are absent.
            if ask is None or bid is None:
                sides=m.get("marketSides") or []
                over=next((s for s in sides if isinstance(s,Mapping) and str(s.get("description") or "").lower()=="over"),None)
                if isinstance(over,Mapping):
                    ask=ask or _d(((over.get("quote") or {}).get("value") if isinstance(over.get("quote"),Mapping) else None))
                    bid=bid or _d(over.get("price"))
            if ask is None or bid is None or not (D("0")<=bid<ask<=ONE):continue
            theta=max(D("0.05"),_d(m.get("feeCoefficient")) or D("0.05"))
            out.append({"key":(date,a.lower(),b.lower(),line),"event":str(e.get("title") or ""),"line":line,"market_id":str(m.get("id") or slug),"slug":slug,"yes_ask":ask,"no_ask":ONE-bid,"theta":theta,"min_qty":_d(m.get("minimumTradeQty"))})
    return out


def _date_teams_from_k_event(ticker:str)->tuple[str,str,str]|None:
    # KXNFLTOTAL-26AUG23SEATEN. Team split is inferred later against Poly/Gemini keys.
    m=re.match(r"^KXNFLTOTAL-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$",ticker.upper())
    if not m:return None
    yy,mon,dd,teams=m.groups();months={x:i+1 for i,x in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"))}
    return f"20{yy}-{months[mon]:02d}-{int(dd):02d}",teams,""


def _poly_index(rows:list[dict[str,Any]])->dict[tuple[str,frozenset[str],D],list[dict[str,Any]]]:
    out={}
    for r in rows:
        date,a,b,line=r["key"];out.setdefault((date,frozenset((a,b)),line),[]).append(r)
    return out


def _gem_key(pair, symbol:str)->tuple[str,frozenset[str],D]|None:
    # Parse GEMI-NFL-YYMMDDHHMM-AWAY-HOME-T-Oxx and use the date encoded by the
    # exact Kalshi event ticker already joined by _pairs, avoiding UTC/local drift.
    m=re.match(r"^KXNFLTOTAL-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$",pair.kalshi.split("-",3)[0]+"-"+pair.kalshi.split("-",3)[1] if False else "")
    sm=re.match(r"^GEMI-NFL-\d{10}-([A-Z]{2,4})-([A-Z]{2,4})-T-",symbol)
    km=re.match(r"^KXNFLTOTAL-(\d{2})([A-Z]{3})(\d{2})",pair.kalshi)
    if not sm or not km:return None
    a,b=sm.groups();yy,mon,dd=km.groups();months={x:i+1 for i,x in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"))}
    return (f"20{yy}-{months[mon]:02d}-{int(dd):02d}",frozenset((a.lower(),b.lower())),pair.line)


async def run_once()->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gevents,kevents,pevents=await asyncio.gather(fetch_active_sports_events(http),_kalshi_events(http),_poly_events(http))
    grows=_gemini_rows(gevents);gpairs=_pairs(grows,kevents);gc=_gcontracts(gevents);km=_kmarkets(kevents);prows=_poly_rows(pevents);pidx=_poly_index(prows)
    groups=[]
    for pair in gpairs:
        key=_gem_key(pair,pair.gemini)
        if key is None:continue
        g=gc.get(pair.gemini);k=kalshi_nested_top_book(km.get(pair.kalshi,{}))
        if not isinstance(g,Mapping) or k is None:continue
        prices=g.get("prices") if isinstance(g.get("prices"),Mapping) else {};ga=_d(prices.get("bestAsk"));gb=_d(prices.get("bestBid"))
        if ga is None or gb is None or not (D("0")<=gb<ga<=ONE):continue
        venues={"gemini":{"id":pair.gemini,"over":ga,"under":ONE-gb,"over_fee":lambda p:gemini_predictions_taker_fee(p,ONE),"under_fee":lambda p:gemini_predictions_taker_fee(p,ONE)},"kalshi":{"id":pair.kalshi,"over":k.yes_ask,"under":k.no_ask,"over_fee":lambda p:nfl_kalshi_taker_fee(p,ONE),"under_fee":lambda p:nfl_kalshi_taker_fee(p,ONE)}}
        poly_matches=pidx.get(key,[])
        if len(poly_matches)==1:
            pr=poly_matches[0];theta=pr["theta"];venues["polymarket"]={"id":pr["market_id"],"slug":pr["slug"],"over":pr["yes_ask"],"under":pr["no_ask"],"over_fee":lambda p,t=theta:_poly_fee(p,t),"under_fee":lambda p,t=theta:_poly_fee(p,t)}
        plans=[]
        for ov,a in venues.items():
            for uv,b in venues.items():
                if ov==uv:continue
                op,up=a["over"],b["under"];of=a["over_fee"](op);uf=b["under_fee"](up);cost=op+up+of+uf
                plans.append({"over_venue":ov,"over_market":a["id"],"over_price":str(op),"under_venue":uv,"under_market":b["id"],"under_price":str(up),"fees":str(of+uf),"combined_cost":str(cost),"locked_profit_if_rules_equivalent":str(ONE-cost)})
        plans.sort(key=lambda r:D(r["locked_profit_if_rules_equivalent"]),reverse=True)
        groups.append({"event":pair.event_title,"line":str(pair.line),"venues":sorted(venues),"best":plans[0] if plans else None})
    priced=[g for g in groups if g["best"]];priced.sort(key=lambda g:D(g["best"]["locked_profit_if_rules_equivalent"]),reverse=True)
    below100=sum(1 for g in priced if D(g["best"]["over_price"])+D(g["best"]["under_price"])<ONE);ge3=sum(1 for g in priced if D(g["best"]["locked_profit_if_rules_equivalent"])>=D("0.03"));positive=sum(1 for g in priced if D(g["best"]["locked_profit_if_rules_equivalent"])>0)
    result={"status":"NFL_THREE_VENUE_DYNAMIC_TOTAL_SNAPSHOT","gemini_total_contracts":len(grows),"kalshi_total_markets":sum(len(e.get("markets") or []) for e in kevents),"polymarket_nfl_full_game_totals":len(prows),"exact_gk_counterparts":len(gpairs),"exact_groups_priced":len(priced),"groups_with_polymarket_exact_counterpart":sum(1 for g in priced if "polymarket" in g["venues"]),"opportunities_raw_below_100c":below100,"opportunities_after_fee_positive":positive,"opportunities_meeting_3c":ge3,"top":priced[:40],"classification":"DISCOVERED_PRICED_RULES_UNCERTIFIED_FINAL_DEPTH_REQUIRED","order_entry":False,"realized_pnl":"0"}
    out=json.dumps(result,separators=(",",":"));logger.info("NFL_THREE_VENUE_DYNAMIC_TOTAL_SNAPSHOT %s",out);print("NFL_THREE_VENUE_DYNAMIC_TOTAL_SNAPSHOT "+out,flush=True);return result


def main()->None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s");asyncio.run(run_once())

if __name__=="__main__":main()
