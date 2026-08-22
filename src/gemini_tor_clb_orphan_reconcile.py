"""Incident-only reconciler for campaign gcombo-2608221328-torclb-rest1.

Cancels only the exact campaign maker order if it is still active. If the combo
filled while its monitor was replaced, complete the two exact q=1 complements
only when the original >=3c same-Gemini cover still passes; otherwise flatten the
combo with SELL LIMIT+FOK. This module never opens a fresh combo position.
"""
from __future__ import annotations

import asyncio, json, time
from decimal import Decimal
from typing import Any, Mapping

import src.gemini_extreme_combo_live_canary as c
import src.gemini_tor_clb_q1_resting_canary as live
from src.gemini_prediction_auth import private_rest_request

D=Decimal
CAMPAIGN="gcombo-2608221328-torclb-rest1"
CLIENT_ID=f"{CAMPAIGN}-c"[:36]
COMBO=live.COMBO

async def _orders(path:str, params:dict[str,Any])->list[dict[str,Any]]:
    r=await private_rest_request("POST",path,params=params,timeout=6.0)
    if r.status_code!=200:return []
    try:b=r.json()
    except Exception:return []
    return [dict(x) for x in (b.get("orders",[]) if isinstance(b,Mapping) else []) if isinstance(x,Mapping)]

async def _find_exact()->tuple[str,dict[str,Any]]|None:
    now=int(time.time()*1000)
    queries=[
        ("active","/v1/prediction-markets/orders/active",{"symbol":COMBO,"limit":100,"offset":0}),
        ("filled","/v1/prediction-markets/orders/history",{"status":"filled","symbol":COMBO,"limit":100,"from":now-7200000,"to":now+1000}),
        ("cancelled","/v1/prediction-markets/orders/history",{"status":"cancelled","symbol":COMBO,"limit":100,"from":now-7200000,"to":now+1000}),
    ]
    for source,path,params in queries:
        for row in await _orders(path,params):
            if str(row.get("clientOrderId") or "")==CLIENT_ID:
                return source,row
    return None

def _oid(row:Mapping[str,Any])->str:
    return str(row.get("orderId") or row.get("id") or "")

def _filled(row:Mapping[str,Any])->D:
    for key in ("filledQuantity","filled_quantity","executedQuantity"):
        try:
            if row.get(key) is not None:return D(str(row[key]))
        except Exception:pass
    return D("0")

async def run_once()->dict[str,Any]:
    out={"status":"GEMINI_TOR_CLB_ORPHAN_RECONCILE","campaign":CAMPAIGN,"client_order_id":CLIENT_ID,"new_combo_order":False,"risk_reduction":True,"realized":False}
    session=c.GeminiTradingSession()
    try:
        await session.connect()
        found=await _find_exact();out["found_source"]=found[0] if found else None
        if found:
            source,row=found;out["found_status"]=str(row.get("status") or "");out["found_filled_quantity"]=str(_filled(row));out["order_id"]=_oid(row)
            if source=="active":
                oid=_oid(row)
                if not oid:
                    out["classification"]="HARD_HALT_ACTIVE_ORDER_ID_MISSING";print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out
                try:
                    cancel=await session.request("order.cancel",{"orderId":oid},timeout=4.0)
                    out["cancel_ws_status"]=cancel.get("status")
                except Exception as exc:
                    r=await private_rest_request("POST","/v1/prediction-markets/order/cancel",params={"orderId":int(oid) if oid.isdigit() else oid},timeout=6.0)
                    out["cancel_rest_status"]=r.status_code
                    if r.status_code!=200:
                        out["classification"]="HARD_HALT_EXACT_CANCEL_FAILED";out["cancel_exception"]=f"{type(exc).__name__}:{str(exc)[:160]}";print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out
                await asyncio.sleep(0.5)

        pos=await live.positions();out["positions_after_cancel"]=pos
        combo_pos=[x for x in pos if str(x.get("symbol") or x.get("instrumentSymbol") or "")==COMBO]
        hedge_pos=[x for x in pos if str(x.get("symbol") or x.get("instrumentSymbol") or "") in {h["symbol"] for h in live.HEDGES}]
        if not combo_pos:
            out["classification"]="ORPHAN_ORDER_CANCELED_OR_ALREADY_TERMINAL_NO_POSITION" if found else "NO_CAMPAIGN_ORDER_NO_POSITION"
            print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out

        # Combo position exists: either already hedged or must be handled now.
        if len(hedge_pos)==2:
            out["classification"]="FILLED_BASKET_ALREADY_HEDGED_NEEDS_SETTLEMENT_RECONCILE"
            print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out
        state=await live.hedge_state();out["fresh_hedge_state"]=state
        if state.get("ok") and not hedge_pos:
            h1,h2=await asyncio.gather(live.buy_hedge(session,live.HEDGES[0],f"{CAMPAIGN}-orh1"),live.buy_hedge(session,live.HEDGES[1],f"{CAMPAIGN}-orh2"))
            out["hedge1"]=h1;out["hedge2"]=h2
            r1=h1.get("result") if isinstance(h1,Mapping) else None;r2=h2.get("result") if isinstance(h2,Mapping) else None
            ok1=isinstance(r1,Mapping) and str(r1.get("status") or "")=="FILLED" and D(str(r1.get("filled_quantity") or "0"))==D("1")
            ok2=isinstance(r2,Mapping) and str(r2.get("status") or "")=="FILLED" and D(str(r2.get("filled_quantity") or "0"))==D("1")
            if ok1 and ok2:
                out["classification"]="FILLED_NOT_SETTLED_ORPHAN_RECOVERED_WITH_HEDGES";print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out
            if ok1:out["hedge1_flatten"]=await live.sell_yes_fok(session,live.HEDGES[0]["symbol"],f"{CAMPAIGN}-orfh1")
            if ok2:out["hedge2_flatten"]=await live.sell_yes_fok(session,live.HEDGES[1]["symbol"],f"{CAMPAIGN}-orfh2")

        # Fresh cover unavailable or hedge completion failed: close combo q=1.
        out["combo_flatten"]=await live.sell_yes_fok(session,COMBO,f"{CAMPAIGN}-orfc")
        out["classification"]="ORPHAN_COMBO_FLATTEN_ATTEMPTED"
        print("GEMINI_TOR_CLB_ORPHAN_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True);return out
    finally:
        await session.close()

if __name__=="__main__":asyncio.run(run_once())
