"""Combo-first q=1 Gemini live canary for No Albon + No Morikawa.

Key safety change: the maker combo is posted FIRST. No hedge is bought unless the
combo fills, so a no-fill probe has no hedge round-trip cost. After a combo fill,
hedges are bought immediately with fresh LIMIT+FOK orders. Target caps preserve
>=3c modeled lock; bounded emergency caps prioritize neutralizing exposure if the
book moves after the combo fill. DISARMED unless both campaign arms match.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Any, Mapping

import httpx
import src.gemini_extreme_combo_live_canary as c
from src.gemini_prediction_auth import private_rest_request

D=Decimal
BASE="https://api.gemini.com"
COMBO="GEMI-CMB-260824-3A4A2AAB2334D23D-CMB"
COMBO_PRICE=D("0.89")
TARGET_LOCK=D("0.03")
HEDGES=[
    {"symbol":"GEMI-F1-DUTGP-WIN-20260823-ALB","outcome":"YES","target":D("0.02"),"emergency":D("0.10")},
    {"symbol":"GEMI-GOLF-BMW-WIN-20260823-MORIKAWA","outcome":"YES","target":D("0.03"),"emergency":D("0.10")},
]


def emit(out:dict[str,Any])->None:
    print("GEMINI_ALBMOR_COMBO_FIRST_CANARY "+json.dumps(out,separators=(",",":"),default=str),flush=True)

async def positions()->list[dict[str,Any]]:
    r=await private_rest_request("POST","/v1/prediction-markets/positions",params={},timeout=8.0)
    if r.status_code!=200:return [{"error":f"positions_http_{r.status_code}"}]
    try:b=r.json()
    except Exception:return [{"error":"positions_parse"}]
    syms={COMBO,*(x["symbol"] for x in HEDGES)}
    return [dict(x) for x in (b.get("positions",[]) if isinstance(b,Mapping) else []) if isinstance(x,Mapping) and str(x.get("symbol") or x.get("instrumentSymbol") or "") in syms]

async def book(sym:str)->dict[str,Any]:
    async with httpx.AsyncClient(timeout=8.0) as h:
        r=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":20,"limit_asks":20})
    try:b=r.json()
    except Exception:b={}
    return b if isinstance(b,Mapping) else {}

def top_ask(b:Mapping[str,Any])->tuple[D,D]|None:
    rows=[]
    for x in b.get("asks",[]) or []:
        if not isinstance(x,Mapping):continue
        try:p=D(str(x.get("price"))); q=D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
        except Exception:continue
        if p>0 and q>0:rows.append((p,q))
    return min(rows,key=lambda z:z[0]) if rows else None

def top_bid(b:Mapping[str,Any])->tuple[D,D]|None:
    rows=[]
    for x in b.get("bids",[]) or []:
        if not isinstance(x,Mapping):continue
        try:p=D(str(x.get("price"))); q=D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
        except Exception:continue
        if p>0 and q>0:rows.append((p,q))
    return max(rows,key=lambda z:z[0]) if rows else None

async def sell_yes_fok(session:c.GeminiTradingSession,sym:str,cid:str)->dict[str,Any]:
    b=await book(sym); bid=top_bid(b)
    if bid is None or bid[1]<D("1"):return {"status":"NO_EXIT_BID"}
    p,depth=bid
    params={"symbol":sym,"side":"SELL","type":"LIMIT","timeInForce":"FOK","price":str(p),"quantity":"1","eventOutcome":"YES","clientOrderId":cid[:36]}
    res=await session.place_fok(params,terminal_timeout=3.0)
    return {"best_bid":str(p),"depth":str(depth),"result":res.as_dict()}

async def buy_hedge(session:c.GeminiTradingSession,spec:dict[str,Any],cid:str)->dict[str,Any]:
    b=await book(spec["symbol"]); ask=top_ask(b)
    if ask is None or ask[1]<D("1"):return {"status":"NO_Q1_ASK"}
    p,depth=ask
    if p>spec["emergency"]:return {"status":"ASK_ABOVE_EMERGENCY_CAP","ask":str(p),"cap":str(spec["emergency"])}
    params={"symbol":spec["symbol"],"side":"BUY","type":"LIMIT","timeInForce":"FOK","price":str(p),"quantity":"1","eventOutcome":spec["outcome"],"clientOrderId":cid[:36]}
    res=await session.place_fok(params,terminal_timeout=3.0)
    return {"ask":str(p),"depth":str(depth),"target_cap":str(spec["target"]),"emergency_cap":str(spec["emergency"]),"result":res.as_dict()}

async def run_once()->dict[str,Any]:
    campaign=os.getenv("GEMINI_COMBO_CAMPAIGN_ID","").strip(); arm=os.getenv("GEMINI_COMBO_ARM_CAMPAIGN","").strip(); enabled=os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1","").strip().upper()=="YES"
    ttl=max(20,min(60,int(os.getenv("GEMINI_COMBO_MAKER_TTL_SECONDS","30"))))
    out={"status":"GEMINI_ALBMOR_COMBO_FIRST_CANARY","campaign":campaign,"armed":bool(enabled and campaign and arm==campaign),"combo":COMBO,"combo_price":str(COMBO_PRICE),"target_locked":str(TARGET_LOCK),"ttl":ttl,"order_entry":False,"realized":False,"classification":"DISARMED"}
    if not out["armed"]:emit(out);return out

    before=await positions(); out["positions_before"]=before
    if before:
        out["classification"]="LIVE_GATE_BLOCKED_EXISTING_POSITION";emit(out);return out
    ids={"combo":f"{campaign}-c"[:36],"h1":f"{campaign}-h1"[:36],"h2":f"{campaign}-h2"[:36]}
    # Reuse the proven terms/balance/idempotency/depth/fee gate with target caps.
    c.COMBO=COMBO; c.HEDGES=[{"symbol":HEDGES[0]["symbol"],"outcome":"YES","max_price":HEDGES[0]["target"]},{"symbol":HEDGES[1]["symbol"],"outcome":"YES","max_price":HEDGES[1]["target"]}]; c.COMBO_MAKER_PRICE=COMBO_PRICE; c.TARGET_LOCK=TARGET_LOCK; c.QTY=D("1")
    pre=await c._fresh_preflight(ids); out["preflight"]=pre
    if not pre.get("pass"):
        out["classification"]="LIVE_GATE_BLOCKED";emit(out);return out

    session=c.GeminiTradingSession(); out["order_entry"]=True
    try:
        await session.connect()
        combo_params={"symbol":COMBO,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(COMBO_PRICE),"quantity":"1","eventOutcome":"YES","clientOrderId":ids["combo"]}
        cr=await c._maker_with_ttl(session,combo_params,ttl_seconds=ttl); out["combo_result"]=cr
        cq=c._d(cr.get("filled_quantity")) or D("0"); cs=str(cr.get("status") or "UNKNOWN").upper()
        if cs=="UNKNOWN" or cq not in {D("0"),D("1")}:
            out["classification"]="LIVE_HARD_HALT_AMBIGUOUS_COMBO";emit(out);return out
        if not (cs=="FILLED" and cq==D("1")):
            out["classification"]="LIVE_PROBE_NO_COMBO_FILL_ZERO_HEDGE_COST";emit(out);return out

        h1=await buy_hedge(session,HEDGES[0],ids["h1"]); out["hedge1"]=h1
        r1=h1.get("result") if isinstance(h1,Mapping) else None
        if not isinstance(r1,Mapping) or str(r1.get("status") or "")!="FILLED" or D(str(r1.get("filled_quantity") or "0"))!=D("1"):
            out["combo_flatten"]=await sell_yes_fok(session,COMBO,f"{campaign}-fc")
            out["classification"]="LIVE_HEDGE1_FAILED_COMBO_FLATTEN_ATTEMPTED";emit(out);return out

        h2=await buy_hedge(session,HEDGES[1],ids["h2"]); out["hedge2"]=h2
        r2=h2.get("result") if isinstance(h2,Mapping) else None
        if not isinstance(r2,Mapping) or str(r2.get("status") or "")!="FILLED" or D(str(r2.get("filled_quantity") or "0"))!=D("1"):
            out["hedge1_flatten"]=await sell_yes_fok(session,HEDGES[0]["symbol"],f"{campaign}-fh1")
            out["combo_flatten"]=await sell_yes_fok(session,COMBO,f"{campaign}-fc")
            out["classification"]="LIVE_HEDGE2_FAILED_FLATTEN_ATTEMPTED";emit(out);return out

        def cost(r:Mapping[str,Any],fallback:D)->D:
            p=c._d(r.get("average_fill_price")) or fallback; f=c._d(r.get("fee_paid")); f=f if f is not None else c._fee(D("0.07"),p); return p+f
        cp=c._d(cr.get("average_fill_price")) or COMBO_PRICE; cf=c._d(cr.get("fee_paid")); cf=cf if cf is not None else c._fee(D("0.0175"),cp)
        total=cp+cf+cost(r1,c._d(h1.get("ask")) or HEDGES[0]["target"])+cost(r2,c._d(h2.get("ask")) or HEDGES[1]["target"]); locked=D("1")-total
        out["actual_or_conservative_all_in"]=str(total); out["modeled_locked_profit"]=str(locked); out["classification"]="FILLED_NOT_SETTLED" if locked>=TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET"; out["settlement_identity"]="AB+(1-A)+(1-B)>=1"
        emit(out);return out
    except Exception as exc:
        out["classification"]="LIVE_HARD_HALT_EXCEPTION"; out["exception"]=f"{type(exc).__name__}:{str(exc)[:220]}"; emit(out);return out
    finally:
        await session.close()

if __name__=="__main__":
    print("GEMINI_ALBMOR_COMBO_FIRST_BOOT",flush=True)
    asyncio.run(run_once())
