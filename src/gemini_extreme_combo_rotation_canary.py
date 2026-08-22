"""Serial q=1 combo-first rotation across fresh F1 x Golf extreme combos.

No hedge is bought unless a combo maker order fills. Each no-fill candidate is
cancelled before the next candidate. Stops after the first fill or any ambiguity.
DISARMED unless both campaign arms match. This is a bounded live canary, not an
always-on executor.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Any, Mapping

import httpx
import src.gemini_extreme_combo_live_canary as c
import src.gemini_albmor_combo_first_canary as cf
from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

D=Decimal
PATH="/v1/prediction-markets/combos"
BASE="https://api.gemini.com"
GOLF={"contractId":"478679","symbol":"GEMI-GOLF-BMW-WIN-20260823-MORIKAWA","label":"Collin Morikawa","target":D("0.03"),"emergency":D("0.10")}
F1=[
    {"contractId":"396739","symbol":"GEMI-F1-DUTGP-WIN-20260823-ALB","label":"Alexander Albon"},
    {"contractId":"396740","symbol":"GEMI-F1-DUTGP-WIN-20260823-ALO","label":"Fernando Alonso"},
    {"contractId":"396742","symbol":"GEMI-F1-DUTGP-WIN-20260823-BEA","label":"Oliver Bearman"},
    {"contractId":"396743","symbol":"GEMI-F1-DUTGP-WIN-20260823-BOR","label":"Gabriel Bortoleto"},
    {"contractId":"396744","symbol":"GEMI-F1-DUTGP-WIN-20260823-BOT","label":"Valtteri Bottas"},
]
COMBO_PRICE=D("0.89")
TARGET_LOCK=D("0.03")


def emit(out:dict[str,Any])->None:
    print("GEMINI_EXTREME_COMBO_ROTATION_CANARY "+json.dumps(out,separators=(",",":"),default=str),flush=True)

async def create_combo(f1:dict[str,Any])->tuple[str|None,dict[str,Any]]:
    meta={"driver":f1["label"],"combo_definition_write":True,"order_entry":False}
    if not has_credentials():meta["error"]="NO_CREDENTIALS";return None,meta
    payload={"legs":[{"contractId":f1["contractId"],"requiredOutcome":"No"},{"contractId":GOLF["contractId"],"requiredOutcome":"No"}]}
    key,secret=credentials(); headers=rest_auth_headers(key,secret,PATH,params=payload); headers["Content-Type"]="application/json"
    async with httpx.AsyncClient(timeout=20.0) as h:r=await h.post(f"{BASE}{PATH}",headers=headers,json=payload)
    try:b=r.json()
    except Exception:b={}
    combo=b.get("combo") if isinstance(b,Mapping) and isinstance(b.get("combo"),Mapping) else {}
    sym=str(combo.get("instrumentSymbol") or "") or None
    meta.update({"http_status":r.status_code,"already_existed":b.get("alreadyExisted") if isinstance(b,Mapping) else None,"instrument_symbol":sym,"registered":combo.get("instrumentRegistered"),"combo_status":combo.get("status"),"error_summary":str(b.get("message") or b.get("error") or "")[:160] if isinstance(b,Mapping) else ""})
    if r.status_code not in {200,201} or not sym or combo.get("instrumentRegistered") is not True or str(combo.get("status") or "").lower()!="active":return None,meta
    return sym,meta

async def current_positions(symbols:set[str])->list[dict[str,Any]]:
    from src.gemini_prediction_auth import private_rest_request
    r=await private_rest_request("POST","/v1/prediction-markets/positions",params={},timeout=8.0)
    if r.status_code!=200:return [{"error":f"positions_http_{r.status_code}"}]
    try:b=r.json()
    except Exception:return [{"error":"positions_parse"}]
    return [dict(x) for x in (b.get("positions",[]) if isinstance(b,Mapping) else []) if isinstance(x,Mapping) and str(x.get("symbol") or x.get("instrumentSymbol") or "") in symbols]

async def run_once()->dict[str,Any]:
    campaign=os.getenv("GEMINI_COMBO_CAMPAIGN_ID","").strip(); arm=os.getenv("GEMINI_COMBO_ARM_CAMPAIGN","").strip(); enabled=os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1","").strip().upper()=="YES"
    ttl=max(15,min(30,int(os.getenv("GEMINI_COMBO_MAKER_TTL_SECONDS","20"))))
    out={"status":"GEMINI_EXTREME_COMBO_ROTATION_CANARY","campaign":campaign,"armed":bool(enabled and campaign and arm==campaign),"candidate_count":len(F1),"combo_price":str(COMBO_PRICE),"target_locked":str(TARGET_LOCK),"ttl_per_candidate":ttl,"order_entry":False,"realized":False,"results":[],"classification":"DISARMED"}
    if not out["armed"]:emit(out);return out

    all_syms={GOLF["symbol"],*(x["symbol"] for x in F1)}
    prior=await current_positions(all_syms); out["positions_before"]=prior
    if prior:out["classification"]="LIVE_GATE_BLOCKED_EXISTING_POSITION";emit(out);return out

    session=c.GeminiTradingSession(); out["order_entry"]=True
    try:
        await session.connect()
        for i,f1 in enumerate(F1,1):
            combo,meta=await create_combo(f1); row={"index":i,"driver":f1["label"],"combo_create":meta}
            if not combo:
                row["classification"]="COMBO_CREATE_BLOCKED"; out["results"].append(row); continue
            h1={"symbol":f1["symbol"],"outcome":"YES","target":D("0.02"),"emergency":D("0.10")}; h2={"symbol":GOLF["symbol"],"outcome":"YES","target":GOLF["target"],"emergency":GOLF["emergency"]}
            ids={"combo":f"{campaign}-{i}-c"[:36],"h1":f"{campaign}-{i}-h1"[:36],"h2":f"{campaign}-{i}-h2"[:36]}
            c.COMBO=combo; c.HEDGES=[{"symbol":h1["symbol"],"outcome":"YES","max_price":h1["target"]},{"symbol":h2["symbol"],"outcome":"YES","max_price":h2["target"]}]; c.COMBO_MAKER_PRICE=COMBO_PRICE; c.TARGET_LOCK=TARGET_LOCK; c.QTY=D("1")
            pre=await c._fresh_preflight(ids); row["preflight"]=pre
            if not pre.get("pass"):
                row["classification"]="LIVE_GATE_BLOCKED"; out["results"].append(row); continue
            params={"symbol":combo,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(COMBO_PRICE),"quantity":"1","eventOutcome":"YES","clientOrderId":ids["combo"]}
            cr=await c._maker_with_ttl(session,params,ttl_seconds=ttl); row["combo_result"]=cr
            cq=c._d(cr.get("filled_quantity")) or D("0"); cs=str(cr.get("status") or "UNKNOWN").upper()
            if cs=="UNKNOWN" or cq not in {D("0"),D("1")}:
                row["classification"]="LIVE_HARD_HALT_AMBIGUOUS_COMBO";out["results"].append(row);out["classification"]=row["classification"];emit(out);return out
            if not (cs=="FILLED" and cq==D("1")):
                row["classification"]="NO_COMBO_FILL_ZERO_HEDGE_COST";out["results"].append(row);continue

            r1=await cf.buy_hedge(session,h1,ids["h1"]);row["hedge1"]=r1
            rr1=r1.get("result") if isinstance(r1,Mapping) else None
            if not isinstance(rr1,Mapping) or str(rr1.get("status") or "")!="FILLED" or D(str(rr1.get("filled_quantity") or "0"))!=D("1"):
                row["combo_flatten"]=await cf.sell_yes_fok(session,combo,f"{campaign}-{i}-fc");row["classification"]="LIVE_HEDGE1_FAILED_COMBO_FLATTEN_ATTEMPTED";out["results"].append(row);out["classification"]=row["classification"];emit(out);return out
            r2=await cf.buy_hedge(session,h2,ids["h2"]);row["hedge2"]=r2
            rr2=r2.get("result") if isinstance(r2,Mapping) else None
            if not isinstance(rr2,Mapping) or str(rr2.get("status") or "")!="FILLED" or D(str(rr2.get("filled_quantity") or "0"))!=D("1"):
                row["hedge1_flatten"]=await cf.sell_yes_fok(session,h1["symbol"],f"{campaign}-{i}-fh1");row["combo_flatten"]=await cf.sell_yes_fok(session,combo,f"{campaign}-{i}-fc");row["classification"]="LIVE_HEDGE2_FAILED_FLATTEN_ATTEMPTED";out["results"].append(row);out["classification"]=row["classification"];emit(out);return out
            def cost(r:Mapping[str,Any],fallback:D)->D:
                p=c._d(r.get("average_fill_price")) or fallback; f=c._d(r.get("fee_paid")); f=f if f is not None else c._fee(D("0.07"),p); return p+f
            cp=c._d(cr.get("average_fill_price")) or COMBO_PRICE; cfv=c._d(cr.get("fee_paid")); cfv=cfv if cfv is not None else c._fee(D("0.0175"),cp)
            total=cp+cfv+cost(rr1,c._d(r1.get("ask")) or h1["target"])+cost(rr2,c._d(r2.get("ask")) or h2["target"]); locked=D("1")-total
            row["all_in"]=str(total);row["locked_profit"]=str(locked);row["classification"]="FILLED_NOT_SETTLED" if locked>=TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET";out["results"].append(row);out["classification"]=row["classification"];out["winning_candidate_index"]=i;emit(out);return out
        out["classification"]="ROTATION_COMPLETE_NO_FILL_ZERO_HEDGE_COST";emit(out);return out
    except Exception as exc:
        out["classification"]="LIVE_HARD_HALT_EXCEPTION";out["exception"]=f"{type(exc).__name__}:{str(exc)[:220]}";emit(out);return out
    finally:
        await session.close()

if __name__=="__main__":
    print("GEMINI_EXTREME_COMBO_ROTATION_BOOT",flush=True)
    asyncio.run(run_once())
