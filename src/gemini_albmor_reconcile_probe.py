"""Read-only reconciliation for the Albon/Morikawa live canary incident."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx
from src.gemini_prediction_auth import private_rest_request

BASE = "https://api.gemini.com"
SYMBOLS = [
    "GEMI-F1-DUTGP-WIN-20260823-ALB",
    "GEMI-GOLF-BMW-WIN-20260823-MORIKAWA",
    "GEMI-CMB-260824-3A4A2AAB2334D23D-CMB",
]

async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {"status":"GEMINI_ALBMOR_RECONCILE","order_entry":False,"symbols":SYMBOLS}
    pos = await private_rest_request("POST", "/v1/prediction-markets/positions", params={}, timeout=8.0)
    out["positions_http"] = pos.status_code
    try: pb = pos.json()
    except Exception: pb = {}
    rows = pb.get("positions") if isinstance(pb, Mapping) else []
    keep=[]
    for r in rows or []:
        if not isinstance(r, Mapping): continue
        sym=str(r.get("symbol") or r.get("instrumentSymbol") or "")
        if sym in SYMBOLS:
            keep.append({
                "symbol":sym,
                "outcome":r.get("outcome"),
                "totalQuantity":r.get("totalQuantity") or r.get("positionQuantity") or r.get("position"),
                "quantityOnHold":r.get("quantityOnHold"),
                "avgPrice":r.get("avgPrice"),
                "marketValue":r.get("marketValue"),
                "unrealizedPnl":r.get("unrealizedPnl"),
                "prices":r.get("prices"),
            })
    out["positions"] = keep
    out["position_count"] = len(keep)

    active={}
    for sym in SYMBOLS:
        r=await private_rest_request("POST","/v1/prediction-markets/orders/active",params={"symbol":sym,"limit":100,"offset":0},timeout=5.0)
        try:b=r.json()
        except Exception:b={}
        active[sym]={"http":r.status_code,"orders":b.get("orders",[]) if isinstance(b,Mapping) else []}
    out["active_orders"] = active

    books={}
    async with httpx.AsyncClient(timeout=8.0) as h:
        for sym in SYMBOLS:
            r=await h.get(f"{BASE}/v1/book/{sym}",params={"limit_bids":20,"limit_asks":20})
            try:b=r.json()
            except Exception:b={}
            books[sym]={"http":r.status_code,"bids":b.get("bids",[])[:5] if isinstance(b,Mapping) else [],"asks":b.get("asks",[])[:5] if isinstance(b,Mapping) else []}
    out["books"] = books
    print("GEMINI_ALBMOR_RECONCILE "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__": asyncio.run(run_once())
