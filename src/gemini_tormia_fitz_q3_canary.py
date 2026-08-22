"""Bounded q=3 combo-first Gemini canary for Toronto/Miami total x Fitzpatrick top-5.

One maker combo order only. No hedges are bought unless the combo fills all 3.
Fresh preflight requires exact q3 hedge depth at target prices and >=3c modeled
locked profit per contract. DISARMED unless both campaign arms match.
"""
from __future__ import annotations

import asyncio, json, os
from decimal import Decimal
from typing import Any, Mapping
import httpx

import src.gemini_extreme_combo_live_canary as c
from src.gemini_prediction_auth import private_rest_request

D=Decimal; BASE="https://api.gemini.com"
QTY=D("3")
COMBO="GEMI-CMB-260824-A17C254D2D52BF6D-CMB"
COMBO_PRICE=D("0.90"); TARGET_LOCK=D("0.03")
HEDGES=[
    {"symbol":"GEMI-MLS-2608222330-TOR-MIA-REGT-O0","outcome":"NO","target":D("0.02"),"emergency":D("0.08")},
    {"symbol":"GEMI-GOLF-BMW-T5-20260823-MATTFITZPATRICK","outcome":"YES","target":D("0.04"),"emergency":D("0.10")},
]

def emit(out:dict[str,Any])->None:
    print("GEMINI_TORMIA_FITZ_Q3_CANARY "+json.dumps(out,separators=(",",":"),default=str),flush=True)

async def positions()->list[dict[str,Any]]:
    r=await private_rest_request("POST","/v1/prediction-markets/positions",params={},timeout=8.0)
    if r.status_code!=200:return [{"error":f"positions_http_{r.status_code}"}]
    try:b=r.json()
    except Exception:return [{"error":"positions_parse"}]
    syms={COMBO,*(x["symbol"] for x in HEDGES)}
    return [dict(x) for x in (b.get("positions",[]) if isinstance(b,Mapping) else []) if isinstance(x,Mapping) and str(x.get("symbol") or x.get("instrumentSymbol") or "") in syms]

async def book(sym:str)->dict[str,Any]:
    async with httpx.AsyncClient(timeout=8.0) as h:
        r=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":30,"limit_asks":30})
    try:b=r.json()
    except Exception:b={}
    return b if isinstance(b,Mapping) else {}

def outcome_ask(b:Mapping[str,Any], outcome:str)->tuple[D,D]|None:
    return c._outcome_ask(b,outcome)

async def fresh_q3_preflight(ids:dict[str,str])->dict[str,Any]:
    out:dict[str,Any]={"pass":False,"order_entry":False,"quantity":"3"}
    terms=await private_rest_request("GET","/v1/prediction-markets/terms/status",timeout=5.0)
    out["terms_http"]=terms.status_code
    try:tb=terms.json()
    except Exception:tb={}
    out["terms_accepted"]=bool(tb.get("hasAcceptedLatest")) if isinstance(tb,Mapping) else False

    balances=await private_rest_request("POST","/v1/balances",timeout=5.0)
    out["balance_http"]=balances.status_code
    usd=D("0")
    try:rows=balances.json()
    except Exception:rows=[]
    if isinstance(rows,list):
        for row in rows:
            if isinstance(row,Mapping) and str(row.get("currency") or "").upper()=="USD":
                usd=c._d(row.get("available")) or D("0");break
    out["usd_available"]=str(usd)

    prior={}
    for cid,sym in ((ids["h1"],HEDGES[0]["symbol"]),(ids["h2"],HEDGES[1]["symbol"]),(ids["combo"],COMBO)):
        found=await c._find_campaign_order(cid,sym)
        if found:
            prior[cid]={"source":found[0],"status":str(found[1].get("status") or ""),"filled_quantity":str(c._row_filled_qty(found[1])),"order_id":c._order_id(found[1])}
    out["prior_campaign_orders"]=prior

    bcombo,b1,b2=await asyncio.gather(book(COMBO),book(HEDGES[0]["symbol"]),book(HEDGES[1]["symbol"]))
    hedge_rows=[];hedge_total=D("0");hedge_ok=True
    for spec,b in zip(HEDGES,(b1,b2)):
        top=outcome_ask(b,spec["outcome"])
        row={"symbol":spec["symbol"],"outcome":spec["outcome"]}
        if top is None:
            row["available"]=False;hedge_ok=False
        else:
            p,depth=top;fee=c._fee(D("0.07"),p,QTY);raw=p*QTY;all_in=raw+fee
            row.update({"available":True,"ask":str(p),"depth":str(depth),"raw_total":str(raw),"fee_total":str(fee),"all_in_total":str(all_in),"target":str(spec["target"])})
            hedge_total+=all_in
            if p>spec["target"] or depth<QTY:hedge_ok=False
        hedge_rows.append(row)

    combo_asks=[z for z in (c._level(x) for x in (bcombo.get("asks") or [])) if z]
    best_combo_ask=min((p for p,_ in combo_asks),default=None)
    maker_fee=c._fee(D("0.0175"),COMBO_PRICE,QTY)
    combo_total=COMBO_PRICE*QTY+maker_fee
    total=combo_total+hedge_total
    locked_total=QTY-total
    locked_pc=locked_total/QTY
    out.update({"hedges":hedge_rows,"hedge_total":str(hedge_total),"combo_best_ask":str(best_combo_ask) if best_combo_ask is not None else None,"combo_maker_price":str(COMBO_PRICE),"combo_raw_total":str(COMBO_PRICE*QTY),"combo_maker_fee_total":str(maker_fee),"combo_all_in_total":str(combo_total),"all_in_if_filled_now":str(total),"locked_total_if_filled_now":str(locked_total),"locked_per_contract_if_filled_now":str(locked_pc),"rule_certification":"COMBO_RULE_13_165_PRODUCT_PLUS_EXACT_COMPONENT_COMPLEMENTS"})
    out["pass"]=bool(out["terms_accepted"] and not prior and hedge_ok and usd>=total and locked_pc>=TARGET_LOCK)
    return out

async def buy_fok(session:c.GeminiTradingSession,spec:dict[str,Any],cid:str)->dict[str,Any]:
    b=await book(spec["symbol"]); top=outcome_ask(b,spec["outcome"])
    if top is None:return {"status":"NO_Q3_ASK"}
    p,depth=top
    if depth<QTY:return {"status":"INSUFFICIENT_Q3_DEPTH","ask":str(p),"depth":str(depth)}
    cap=spec["target"] if p<=spec["target"] else spec["emergency"]
    if p>cap:return {"status":"ASK_ABOVE_EMERGENCY_CAP","ask":str(p),"cap":str(cap)}
    params={"symbol":spec["symbol"],"side":"BUY","type":"LIMIT","timeInForce":"FOK","price":str(cap),"quantity":"3","eventOutcome":spec["outcome"],"clientOrderId":cid[:36]}
    res=await session.place_fok(params,terminal_timeout=3.0)
    return {"observed_ask":str(p),"depth":str(depth),"limit":str(cap),"target":str(spec["target"]),"result":res.as_dict()}

async def sell_yes_fok(session:c.GeminiTradingSession,sym:str,cid:str,qty:D=QTY)->dict[str,Any]:
    b=await book(sym)
    bids=[]
    for x in b.get("bids",[]) or []:
        if not isinstance(x,Mapping):continue
        try:p=D(str(x.get("price")));q=D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
        except Exception:continue
        if p>0 and q>=qty:bids.append((p,q))
    if not bids:return {"status":"NO_EXIT_BID"}
    p,depth=max(bids,key=lambda z:z[0])
    params={"symbol":sym,"side":"SELL","type":"LIMIT","timeInForce":"FOK","price":str(p),"quantity":str(qty),"eventOutcome":"YES","clientOrderId":cid[:36]}
    res=await session.place_fok(params,terminal_timeout=3.0)
    return {"best_bid":str(p),"depth":str(depth),"result":res.as_dict()}

async def run_once()->dict[str,Any]:
    campaign=os.getenv("GEMINI_COMBO_CAMPAIGN_ID","").strip(); arm=os.getenv("GEMINI_COMBO_ARM_CAMPAIGN","").strip(); enabled=os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1","").strip().upper()=="YES"
    ttl=max(30,min(180,int(os.getenv("GEMINI_COMBO_MAKER_TTL_SECONDS","120"))))
    out={"status":"GEMINI_TORMIA_FITZ_Q3_CANARY","campaign":campaign,"armed":bool(enabled and campaign and arm==campaign),"quantity":"3","combo":COMBO,"combo_price":str(COMBO_PRICE),"target_locked_per_contract":str(TARGET_LOCK),"ttl":ttl,"order_entry":False,"realized":False,"classification":"DISARMED"}
    if not out["armed"]:emit(out);return out
    before=await positions();out["positions_before"]=before
    if before:out["classification"]="LIVE_GATE_BLOCKED_EXISTING_POSITION";emit(out);return out

    ids={"combo":f"{campaign}-c"[:36],"h1":f"{campaign}-h1"[:36],"h2":f"{campaign}-h2"[:36]}
    c.QTY=QTY
    pre=await fresh_q3_preflight(ids);out["preflight"]=pre
    if not pre.get("pass"):out["classification"]="LIVE_GATE_BLOCKED";emit(out);return out

    session=c.GeminiTradingSession();out["order_entry"]=True
    try:
        await session.connect()
        combo_params={"symbol":COMBO,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(COMBO_PRICE),"quantity":"3","eventOutcome":"YES","clientOrderId":ids["combo"]}
        cr=await c._maker_with_ttl(session,combo_params,ttl_seconds=ttl);out["combo_result"]=cr
        cq=c._d(cr.get("filled_quantity")) or D("0");cs=str(cr.get("status") or "UNKNOWN").upper()
        if cs=="UNKNOWN" or cq not in {D("0"),QTY}:out["classification"]="LIVE_HARD_HALT_AMBIGUOUS_COMBO";emit(out);return out
        if not (cs=="FILLED" and cq==QTY):out["classification"]="LIVE_PROBE_NO_COMBO_FILL_ZERO_HEDGE_COST";emit(out);return out

        h1,h2=await asyncio.gather(buy_fok(session,HEDGES[0],ids["h1"]),buy_fok(session,HEDGES[1],ids["h2"]));out["hedge1"]=h1;out["hedge2"]=h2
        r1=h1.get("result") if isinstance(h1,Mapping) else None;r2=h2.get("result") if isinstance(h2,Mapping) else None
        ok1=isinstance(r1,Mapping) and str(r1.get("status") or "") == "FILLED" and D(str(r1.get("filled_quantity") or "0"))==QTY
        ok2=isinstance(r2,Mapping) and str(r2.get("status") or "") == "FILLED" and D(str(r2.get("filled_quantity") or "0"))==QTY
        if not (ok1 and ok2):
            out["classification"]="LIVE_HEDGE_FAILURE_AFTER_COMBO_FILL_HARD_HALT"
            if ok1:out["hedge1_flatten"]=await sell_yes_fok(session,HEDGES[0]["symbol"],f"{campaign}-fh1")
            if ok2:out["hedge2_flatten"]=await sell_yes_fok(session,HEDGES[1]["symbol"],f"{campaign}-fh2")
            out["combo_flatten"]=await sell_yes_fok(session,COMBO,f"{campaign}-fc")
            emit(out);return out

        def cost(r:Mapping[str,Any],fallback:D)->D:
            p=c._d(r.get("average_fill_price")) or fallback;f=c._d(r.get("fee_paid"));f=f if f is not None else c._fee(D("0.07"),p,QTY);return p*QTY+f
        cp=c._d(cr.get("average_fill_price")) or COMBO_PRICE;cf=c._d(cr.get("fee_paid"));cf=cf if cf is not None else c._fee(D("0.0175"),cp,QTY)
        total=cp*QTY+cf+cost(r1,c._d(h1.get("observed_ask")) or HEDGES[0]["target"])+cost(r2,c._d(h2.get("observed_ask")) or HEDGES[1]["target"])
        locked_total=QTY-total;locked_pc=locked_total/QTY
        out["actual_or_conservative_all_in"]=str(total);out["locked_total"]=str(locked_total);out["locked_per_contract"]=str(locked_pc);out["classification"]="FILLED_NOT_SETTLED" if locked_pc>=TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET";out["settlement_identity"]="AB+(1-A)+(1-B)>=1";emit(out);return out
    except Exception as exc:
        out["classification"]="LIVE_HARD_HALT_EXCEPTION";out["exception"]=f"{type(exc).__name__}:{str(exc)[:220]}";emit(out);return out
    finally:await session.close()

if __name__=="__main__":
    print("GEMINI_TORMIA_FITZ_Q3_BOOT",flush=True);asyncio.run(run_once())
