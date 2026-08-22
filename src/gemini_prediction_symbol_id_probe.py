"""Read-only public Gemini symbol-detail probe for prediction sports instruments."""
from __future__ import annotations
import asyncio,json
import httpx

SYMBOLS=[
"GEMI-MLB-2608221810-ATL-MIL-M-ATL",
"GEMI-MLB-2608221810-ATL-MIL-M-MIL",
"GEMI-MLB-2608222010-WSH-MIA-M-WSH",
"GEMI-MLB-2608222010-WSH-MIA-M-MIA",
]
async def run_once():
    rows=[]
    async with httpx.AsyncClient(timeout=20) as h:
        for s in SYMBOLS:
            try:
                r=await h.get(f"https://api.gemini.com/v1/symbols/details/{s}")
                try: body=r.json()
                except Exception: body={}
                rows.append({"symbol":s,"status":r.status_code,"keys":sorted(body.keys()) if isinstance(body,dict) else [],"id_fields":{k:v for k,v in body.items() if isinstance(body,dict) and ("id" in k.lower() or "instrument" in k.lower() or "contract" in k.lower())}})
            except Exception as exc:
                rows.append({"symbol":s,"error":f"{type(exc).__name__}:{str(exc)[:100]}"})
    out={"status":"GEMINI_PREDICTION_SYMBOL_ID_PROBE","rows":rows,"read_only":True,"order_entry":False}
    print("GEMINI_PREDICTION_SYMBOL_ID_PROBE "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out
def main(): asyncio.run(run_once())
if __name__=="__main__": main()
