"""Read-only q=3 preflight for the Toronto/Miami x Fitzpatrick combo."""
from __future__ import annotations
import asyncio, json
import src.gemini_tormia_fitz_q3_canary as q3

async def run_once():
    ids={"combo":"q3-preflight-only-c","h1":"q3-preflight-only-h1","h2":"q3-preflight-only-h2"}
    out=await q3.fresh_q3_preflight(ids)
    out.update({"status":"GEMINI_TORMIA_FITZ_Q3_PREFLIGHT","order_entry":False,"realized":False})
    print("GEMINI_TORMIA_FITZ_Q3_PREFLIGHT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__": asyncio.run(run_once())
