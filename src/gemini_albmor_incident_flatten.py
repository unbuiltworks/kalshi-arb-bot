"""Bounded incident recovery: close exactly one YES Albon and one YES Morikawa.

Uses fresh public best bids and SELL LIMIT+FOK, which Gemini prediction markets
support. Stops on ambiguous submission. This is risk reduction only, not a new trade.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any, Mapping

import httpx
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_prediction_auth import private_rest_request

D=Decimal
BASE="https://api.gemini.com"
SYMBOLS=[
    "GEMI-F1-DUTGP-WIN-20260823-ALB",
    "GEMI-GOLF-BMW-WIN-20260823-MORIKAWA",
]

async def _positions() -> dict[str, dict[str, Any]]:
    r=await private_rest_request("POST","/v1/prediction-markets/positions",params={},timeout=8.0)
    if r.status_code!=200: return {}
    try:b=r.json()
    except Exception:return {}
    out={}
    for row in b.get("positions",[]) if isinstance(b,Mapping) else []:
        if not isinstance(row,Mapping): continue
        sym=str(row.get("symbol") or row.get("instrumentSymbol") or "")
        if sym in SYMBOLS and str(row.get("outcome") or "").lower()=="yes": out[sym]=dict(row)
    return out

async def _best_bid(h:httpx.AsyncClient,sym:str)->tuple[D,D]|None:
    r=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":20,"limit_asks":20})
    if r.status_code!=200:return None
    try:b=r.json()
    except Exception:return None
    rows=[]
    for x in b.get("bids",[]) if isinstance(b,Mapping) else []:
        try:p=D(str(x.get("price")));q=D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
        except Exception:continue
        if p>0 and q>0:rows.append((p,q))
    return max(rows,key=lambda z:z[0]) if rows else None

async def run_once()->dict[str,Any]:
    out:dict[str,Any]={"status":"GEMINI_ALBMOR_INCIDENT_FLATTEN","risk_reduction":True,"new_strategy_trade":False,"orders":[]}
    before=await _positions(); out["before"]={s:{"qty":r.get("totalQuantity"),"avgPrice":r.get("avgPrice"),"outcome":r.get("outcome")} for s,r in before.items()}
    session=GeminiTradingSession()
    try:
        await session.connect()
        async with httpx.AsyncClient(timeout=8.0) as h:
            for sym in SYMBOLS:
                row=before.get(sym)
                qty=D(str(row.get("totalQuantity") or "0")) if row else D("0")
                if qty==0:
                    out["orders"].append({"symbol":sym,"skipped":"NO_POSITION"}); continue
                if qty!=D("1"):
                    out["orders"].append({"symbol":sym,"blocked":f"UNEXPECTED_POSITION_{qty}"}); out["classification"]="HARD_HALT_UNEXPECTED_POSITION"; break
                top=await _best_bid(h,sym)
                if top is None or top[1]<D("1"):
                    out["orders"].append({"symbol":sym,"blocked":"NO_Q1_BID"}); out["classification"]="HARD_HALT_NO_EXIT_DEPTH"; break
                price,depth=top
                params={"symbol":sym,"side":"SELL","type":"LIMIT","timeInForce":"FOK","price":str(price),"quantity":"1","eventOutcome":"YES","clientOrderId":f"incflat-{uuid.uuid4().hex[:20]}"}
                res=await session.place_fok(params,terminal_timeout=3.0)
                out["orders"].append({"symbol":sym,"best_bid":str(price),"depth":str(depth),"result":res.as_dict()})
                if res.status=="UNKNOWN":
                    out["classification"]="HARD_HALT_AMBIGUOUS_EXIT"; break
                if res.status!="FILLED" or D(str(res.filled_quantity))!=D("1"):
                    out["classification"]="EXIT_NOT_FILLED"; break
        after=await _positions(); out["after"]={s:{"qty":r.get("totalQuantity"),"avgPrice":r.get("avgPrice"),"outcome":r.get("outcome")} for s,r in after.items()}
        if not after:
            out["classification"]="INCIDENT_FLATTENED"
        elif "classification" not in out:
            out["classification"]="POSITIONS_REMAIN"
        print("GEMINI_ALBMOR_INCIDENT_FLATTEN "+json.dumps(out,separators=(",",":"),default=str),flush=True)
        return out
    finally:
        await session.close()

if __name__=="__main__": asyncio.run(run_once())
