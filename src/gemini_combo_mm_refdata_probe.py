"""Read-only quantity/price grid audit for accepted combo instruments."""
from __future__ import annotations
import asyncio, json
import httpx

BASE="https://api.gemini.com"
SYMBOLS=[
 "GEMI-CMB-260823-605EB3AACEDE12F2-CMB",
 "GEMI-CMB-260823-0C93A84ACACD7C1D-CMB",
 "GEMI-CMB-260823-130D47B64F4671DC-CMB",
 "GEMI-CMB-260823-790AAEFA6CECBA1E-CMB",
]

async def run_once():
    rows=[]
    async with httpx.AsyncClient(timeout=12.0) as h:
        for sym in SYMBOLS:
            r=await h.get(f"{BASE}/v1/prediction-markets/combos/{sym}")
            try:b=r.json()
            except Exception:b={}
            c=b.get("contract",{}) if isinstance(b,dict) else {}
            rows.append({"symbol":sym,"http":r.status_code,"quantityMinimum":c.get("quantityMinimum"),"quantityIncrement":c.get("quantityIncrement"),"priceMinimum":c.get("priceMinimum"),"priceIncrement":c.get("priceIncrement"),"marketState":c.get("marketState"),"status":c.get("status"),"expiryDate":c.get("expiryDate"),"legCount":len(b.get("legs",[])) if isinstance(b,dict) and isinstance(b.get("legs"),list) else None})
    out={"status":"GEMINI_COMBO_MM_REFDATA_PROBE","rows":rows,"read_only":True,"order_entry":False}
    print("GEMINI_COMBO_MM_REFDATA_PROBE "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out
if __name__=="__main__":asyncio.run(run_once())
