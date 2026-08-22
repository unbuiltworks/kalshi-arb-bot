"""Read-only q=3 preflight for the Toronto/Miami x Columbus/Nashville combo."""
from __future__ import annotations
import asyncio, json
import src.gemini_tor_clb_q3_canary as wrap

async def run_once():
    q3=wrap.q3
    out=await q3.fresh_q3_preflight({"combo":"tor-clb-q3-pre-c","h1":"tor-clb-q3-pre-h1","h2":"tor-clb-q3-pre-h2"})
    out.update({"status":"GEMINI_TOR_CLB_Q3_PREFLIGHT","order_entry":False,"realized":False})
    print("GEMINI_TOR_CLB_Q3_PREFLIGHT "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out

if __name__=="__main__":asyncio.run(run_once())
