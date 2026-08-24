"""Read-only Kalshi NCAAF spread/total shape probe. No auth or order entry."""
from __future__ import annotations
import asyncio,json,logging
from typing import Mapping
import httpx
K="https://external-api.kalshi.com/trade-api/v2"
log=logging.getLogger("kalshi_arb.sports_ncaaf_series_shape_probe")
async def main_async():
    out={"status":"SPORTS_NCAAF_SERIES_SHAPE_PROBE","read_only":True,"order_entry":False,"series":{}}
    async with httpx.AsyncClient(timeout=15.0) as h:
        for s in ("KXNCAAFSPREAD","KXNCAAFTOTAL"):
            r=await h.get(f"{K}/events",params={"status":"open","series_ticker":s,"limit":20,"with_nested_markets":"true"});r.raise_for_status()
            ev=[x for x in r.json().get("events",[]) if isinstance(x,Mapping)]
            out["series"][s]={"events":len(ev),"samples":[{"event_ticker":e.get("event_ticker"),"title":e.get("title"),"sub_title":e.get("sub_title"),"markets":[{"ticker":m.get("ticker"),"title":m.get("title"),"yes_sub_title":m.get("yes_sub_title"),"strike_type":m.get("strike_type"),"floor_strike":m.get("floor_strike"),"cap_strike":m.get("cap_strike")} for m in (e.get("markets") or [])[:6] if isinstance(m,Mapping)]} for e in ev[:8]]}
    log.warning("SPORTS_NCAAF_SERIES_SHAPE_PROBE %s",json.dumps(out,separators=(",",":"),default=str))
if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s");asyncio.run(main_async())
