"""Read-only q1/q5/q10 Gemini extreme-combo scanner using true book depth.

Short-horizon sports only. For each high-probability selected outcome with a cheap
exact complement, fetch the public order book and compute executable cumulative
complement cost at q=1/5/10. Then compute the highest combo maker BUY price that
still leaves >=3c locked profit per contract after exact rounded fees.

No combo creation and no order entry.
"""
from __future__ import annotations

import asyncio, json, re, time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from itertools import combinations
from typing import Any, Mapping
import httpx

from src.gemini_sports_inventory import fetch_active_sports_events

D=Decimal; ONE=D("1"); TARGET=D("0.03"); MAKER=D("0.0175"); TAKER=D("0.07")
BASE="https://api.gemini.com"; QS=(1,5,10); HORIZON_H=36

def dec(v:Any)->D|None:
    try:x=D(str(v))
    except Exception:return None
    return x if x.is_finite() else None

def fee_total(price:D, qty:D, rate:D)->D:
    raw=rate*qty*price*(ONE-price)
    return (raw*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")

def numeric_id(c:Mapping[str,Any])->str|None:
    s=str(c.get("id") or "")
    if s.isdigit(): return s
    m=re.fullmatch(r"\d+-(\d+)",s)
    return m.group(1) if m else None

def parse_dt(v:Any)->float|None:
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
    except Exception:return None

def levels(body:Any, side:str)->list[tuple[D,D]]:
    if not isinstance(body,Mapping):return []
    arr=body.get("asks") if side=="YES" else body.get("bids")
    out=[]
    for x in arr or []:
        if not isinstance(x,Mapping):continue
        p=dec(x.get("price")); q=dec(x.get("amount") if x.get("amount") is not None else x.get("quantity"))
        if p is None or q is None or q<=0 or not(D("0")<p<ONE):continue
        # Buying NO consumes YES bids: NO price = 1 - YES bid.
        out.append(((p if side=="YES" else ONE-p),q))
    out.sort(key=lambda z:z[0])
    return out

def executable(levels_:list[tuple[D,D]], qty:int)->dict[str,str]|None:
    rem=D(qty); raw=D("0"); fees=D("0"); worst=D("0"); used=0
    for p,avail in levels_:
        take=min(rem,avail)
        if take<=0:continue
        raw += p*take
        # Conservative: round fee separately per consumed price level.
        fees += fee_total(p,take,TAKER)
        worst=max(worst,p); used+=1; rem-=take
        if rem<=0:break
    if rem>0:return None
    q=D(qty)
    return {"raw_total":str(raw),"fee_total":str(fees),"all_in_total":str(raw+fees),"all_in_per_contract":str((raw+fees)/q),"worst_price":str(worst),"levels_used":str(used)}

def max_combo_price(qty:int, hedge_total:D)->D|None:
    q=D(qty)
    # Price increments are usually 1c; use only legal cent prices here.
    for cents in range(99,0,-1):
        p=D(cents)/D("100")
        total=p*q+fee_total(p,q,MAKER)+hedge_total
        if (q-total)/q >= TARGET:return p
    return None

async def run_once()->dict[str,Any]:
    now=time.time(); horizon=now+HORIZON_H*3600
    async with httpx.AsyncClient(timeout=30.0) as h:
        events=await fetch_active_sports_events(h)
        legs=[]
        for e in events:
            expiry=parse_dt(e.get("expiryDate"))
            if expiry is None or expiry<=now or expiry>horizon:continue
            for c in e.get("contracts") or []:
                if not isinstance(c,Mapping) or str(c.get("status") or "").lower()!="active" or str(c.get("marketState") or "").lower()!="open":continue
                ps=c.get("prices") if isinstance(c.get("prices"),Mapping) else {}
                ask=dec(ps.get("bestAsk")); bid=dec(ps.get("bestBid")); cid=numeric_id(c); sym=str(c.get("instrumentSymbol") or "")
                if ask is None or bid is None or not cid or not sym or not(D("0")<=bid<ask<=ONE):continue
                mid=(ask+bid)/D("2")
                # selected NO => complement YES; selected YES => complement NO.
                if ask<=D("0.05"):
                    legs.append({"event":str(e.get("ticker") or ""),"title":str(e.get("title") or ""),"symbol":sym,"contract_id":cid,"label":str(c.get("label") or ""),"required":"No","complement":"YES","selected_mid":ONE-mid,"expiry":str(c.get("expiryDate") or e.get("expiryDate") or ""),"terms":str(c.get("termsAndConditionsUrl") or ""),"top_complement":ask})
                noask=ONE-bid
                if noask<=D("0.05"):
                    legs.append({"event":str(e.get("ticker") or ""),"title":str(e.get("title") or ""),"symbol":sym,"contract_id":cid,"label":str(c.get("label") or ""),"required":"Yes","complement":"NO","selected_mid":mid,"expiry":str(c.get("expiryDate") or e.get("expiryDate") or ""),"terms":str(c.get("termsAndConditionsUrl") or ""),"top_complement":noask})
        legs.sort(key=lambda x:(x["top_complement"],-x["selected_mid"],x["expiry"]))
        # Keep strongest 60, with enough variety for pair search.
        legs=legs[:60]
        books={}
        sem=asyncio.Semaphore(15)
        async def getbook(s:str):
            async with sem:
                r=await h.get(f"{BASE}/v1/book/{s}",params={"limit_bids":50,"limit_asks":50})
                try:b=r.json()
                except Exception:b={}
                books[s]=(r.status_code,b)
        await asyncio.gather(*(getbook(x["symbol"]) for x in legs))

    for leg in legs:
        status,b=books.get(leg["symbol"],(0,{})); leg["book_http"]=status; leg["depth"]={}
        lv=levels(b,leg["complement"])
        for q in QS: leg["depth"][str(q)]=executable(lv,q)

    plans=[]
    for a,b in combinations(legs,2):
        if a["contract_id"]==b["contract_id"]:continue
        for q in QS:
            ea=a["depth"].get(str(q)); eb=b["depth"].get(str(q))
            if not ea or not eb:continue
            hedge=D(ea["all_in_total"])+D(eb["all_in_total"])
            mp=max_combo_price(q,hedge)
            if mp is None:continue
            qd=D(q); mf=fee_total(mp,qd,MAKER); locked=(qd-(mp*qd+mf+hedge))/qd
            fair=a["selected_mid"]*b["selected_mid"]
            plans.append({"q":q,"maker_price":mp,"maker_fee_total":mf,"hedge_total":hedge,"locked_per_contract":locked,"fair_proxy":fair,"fair_gap":fair-mp,"same_event":a["event"]==b["event"],"expiry_max":max(a["expiry"],b["expiry"]),"legs":[a,b]})
    # Rank fill-likelihood proxy: smallest positive fair gap, then shorter expiry, then q.
    plans.sort(key=lambda p:(abs(p["fair_gap"]),p["expiry_max"],p["q"]))
    def cleandep(d):return d
    def cleanleg(x):
        return {"event":x["event"],"title":x["title"],"symbol":x["symbol"],"contract_id":x["contract_id"],"label":x["label"],"required":x["required"],"complement":x["complement"],"selected_mid":str(x["selected_mid"]),"expiry":x["expiry"],"terms":x["terms"],"depth":x["depth"]}
    def cleanplan(p):return {"q":p["q"],"maker_price":str(p["maker_price"]),"maker_fee_total":str(p["maker_fee_total"]),"hedge_total":str(p["hedge_total"]),"locked_per_contract":str(p["locked_per_contract"]),"fair_proxy":str(p["fair_proxy"]),"fair_gap":str(p["fair_gap"]),"same_event":p["same_event"],"expiry_max":p["expiry_max"],"legs":[cleanleg(x) for x in p["legs"]]}
    best_by_q={}
    for q in QS:
        rows=[p for p in plans if p["q"]==q]
        best_by_q[str(q)]=cleanplan(rows[0]) if rows else None
    out={"status":"GEMINI_EXTREME_COMBO_DEPTH_QUANT","horizon_hours":HORIZON_H,"sports_events":len(events),"extreme_legs_depth_checked":len(legs),"plans_scored":len(plans),"best_by_q":best_by_q,"top_plans":[cleanplan(x) for x in plans[:20]],"target_locked_per_contract":"0.03","maker_rate":"0.0175","taker_rate":"0.07","maker_rebate_counted":False,"read_only":True,"combo_creation":False,"order_entry":False}
    print("GEMINI_EXTREME_COMBO_DEPTH_QUANT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

def main():asyncio.run(run_once())
if __name__=="__main__":main()
