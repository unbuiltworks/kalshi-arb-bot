"""Read-only audit of exact Gemini MLB component contract settlement metadata."""
from __future__ import annotations
import asyncio, json
from typing import Any
import httpx

URL="https://api.gemini.com/v1/prediction-markets/events"
TARGET_EVENTS={"MLB-2608221810-ATL-MIL-M","MLB-2608222010-WSH-MIA-M"}

async def run_once() -> dict[str,Any]:
    async with httpx.AsyncClient(timeout=20.0) as h:
        r=await h.get(URL,params={"status":"active","sport":"baseball","limit":500,"offset":0})
        r.raise_for_status(); body=r.json()
    events=body.get("events") or body.get("data") or []
    rows=[]
    for e in events:
        if not isinstance(e,dict) or str(e.get("ticker") or "") not in TARGET_EVENTS: continue
        contracts=[]
        for c in e.get("contracts") or []:
            if not isinstance(c,dict): continue
            contracts.append({
                "instrumentSymbol":c.get("instrumentSymbol"),
                "label":c.get("label"),
                "status":c.get("status"),
                "marketState":c.get("marketState"),
                "sportsSide":c.get("sportsSide"),
                "expiryDate":c.get("expiryDate"),
                "effectiveDate":c.get("effectiveDate"),
                "termsAndConditionsUrl":c.get("termsAndConditionsUrl"),
                "description":c.get("description"),
            })
        rows.append({
            "ticker":e.get("ticker"),"title":e.get("title"),"status":e.get("status"),
            "startTime":e.get("startTime"),"expiryDate":e.get("expiryDate"),
            "contracts":contracts,
        })
    out={"status":"GEMINI_MLB_TARGET_TERMS_AUDIT","http_status":r.status_code,"targets":rows,"read_only":True,"order_entry":False}
    print("GEMINI_MLB_TARGET_TERMS_AUDIT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

def main(): asyncio.run(run_once())
if __name__=="__main__": main()
