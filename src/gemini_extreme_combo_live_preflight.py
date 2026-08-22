"""Read-only/pre-trade preflight for one accepted Gemini extreme combo canary.

No order entry. Verifies terms, available USD, no active combo order, exact q=1
hedge depth/cost, and conservative maker economics for the accepted cross-event
Toronto-total + Columbus-spread combo.
"""
from __future__ import annotations

import asyncio, json
from decimal import Decimal, ROUND_CEILING
from typing import Any
import httpx

from src.gemini_prediction_auth import private_rest_request

D=Decimal
ONE=D("1")
BASE="https://api.gemini.com"
COMBO="GEMI-CMB-260823-8EC8FBCF7988E34C-CMB"
HEDGES=[
    {"symbol":"GEMI-MLS-2608222330-TOR-MIA-REGT-O0","outcome":"NO"},
    {"symbol":"GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2","outcome":"YES"},
]
MAKER_PRICE=D("0.89")
TARGET=D("0.03")


def fee(rate:D,p:D,qty:D=D("1"))->D:
    raw=rate*qty*p*(ONE-p)
    return (raw*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")

def parse_level(x:Any):
    if not isinstance(x,dict): return None
    try:
        p=D(str(x.get("price"))); q=D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
    except Exception: return None
    if q<=0 or p<=0 or p>=1: return None
    return p,q

def outcome_ask(book:dict[str,Any],outcome:str):
    asks=[z for z in (parse_level(x) for x in (book.get("asks") or [])) if z]
    bids=[z for z in (parse_level(x) for x in (book.get("bids") or [])) if z]
    if outcome=="YES":
        return min(asks,key=lambda z:z[0]) if asks else None
    if outcome=="NO":
        if not bids: return None
        bp,bq=max(bids,key=lambda z:z[0])
        return ONE-bp,bq
    return None

async def run_once()->dict[str,Any]:
    out={"status":"GEMINI_EXTREME_COMBO_LIVE_PREFLIGHT","order_entry":False,"combo":COMBO,"maker_price":str(MAKER_PRICE),"target_locked":str(TARGET)}
    terms=await private_rest_request("GET","/v1/prediction-markets/terms/status",timeout=5.0)
    out["terms_http"]=terms.status_code
    try: tb=terms.json()
    except Exception: tb={}
    out["terms_accepted"]=bool(tb.get("hasAcceptedLatest")) if isinstance(tb,dict) else False

    bal=await private_rest_request("POST","/v1/balances",timeout=5.0)
    out["balance_http"]=bal.status_code
    usd=D("0")
    try: rows=bal.json()
    except Exception: rows=[]
    if isinstance(rows,list):
        for row in rows:
            if isinstance(row,dict) and str(row.get("currency") or "").upper()=="USD":
                usd=D(str(row.get("available") or "0")); break
    out["usd_available"]=str(usd)

    active=await private_rest_request("POST","/v1/prediction-markets/orders/active",params={"symbol":COMBO,"limit":100,"offset":0},timeout=5.0)
    out["active_orders_http"]=active.status_code
    try: ab=active.json()
    except Exception: ab={}
    active_rows=ab.get("orders") or [] if isinstance(ab,dict) else []
    out["active_combo_orders"]=len(active_rows)

    async with httpx.AsyncClient(timeout=8.0) as h:
        async def fetch(symbol:str):
            r=await h.get(f"{BASE}/v1/book/{symbol}",params={"limit_bids":20,"limit_asks":20})
            try: body=r.json()
            except Exception: body={}
            return r.status_code,body
        combo_res, h1, h2=await asyncio.gather(fetch(COMBO),fetch(HEDGES[0]["symbol"]),fetch(HEDGES[1]["symbol"]))
    out["combo_book"]={"http":combo_res[0],"bids":len(combo_res[1].get("bids") or []) if isinstance(combo_res[1],dict) else 0,"asks":len(combo_res[1].get("asks") or []) if isinstance(combo_res[1],dict) else 0}
    hedge_rows=[]; hedge_total=D("0"); depth_ok=True
    for spec,res in zip(HEDGES,[h1,h2]):
        top=outcome_ask(res[1],spec["outcome"]) if res[0]==200 and isinstance(res[1],dict) else None
        row={"symbol":spec["symbol"],"outcome":spec["outcome"],"http":res[0]}
        if top is None:
            row["available"]=False; depth_ok=False
        else:
            p,q=top; f=fee(D("0.07"),p)
            row.update({"available":True,"ask":str(p),"depth":str(q),"fee":str(f),"all_in":str(p+f)})
            hedge_total += p+f
            if q < ONE: depth_ok=False
        hedge_rows.append(row)
    maker_fee=fee(D("0.0175"),MAKER_PRICE)
    total=MAKER_PRICE+maker_fee+hedge_total
    locked=ONE-total
    out.update({"hedges":hedge_rows,"hedge_total":str(hedge_total),"hedge_depth_q1_ok":depth_ok,"maker_fee_conservative":str(maker_fee),"all_in_if_filled_now":str(total),"locked_if_filled_now":str(locked)})
    out["preflight_pass"]=bool(out["terms_accepted"] and usd>=ONE and out["active_combo_orders"]==0 and depth_ok and locked>=TARGET)
    out["rule_certification"]="GEMINI_COMBO_RULE_13_165_PRODUCT_OF_COMPONENT_SETTLEMENT_VALUES_PLUS_COMPLEMENT_HEDGES"
    out["max_prehedge_exposure_if_used"]="0.06"
    print("GEMINI_EXTREME_COMBO_LIVE_PREFLIGHT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__": asyncio.run(run_once())
