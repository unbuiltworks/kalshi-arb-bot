"""Read-only current-feed MLB normalization diagnostic."""
from __future__ import annotations
import asyncio,json
from collections import Counter
import httpx
from src.mlb_game_side_normalization import normalize_gemini_mlb,normalize_polymarket_mlb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.universal_three_venue_sports import fetch_gemini_mlb,fetch_polymarket_mlb,fetch_kalshi_mlb

def compact(rows):
    fam=Counter(r.identity.market_family for r in rows)
    money=[]
    for r in rows:
        if r.identity.market_family=="moneyline" and len(money)<20:
            money.append({"venue":r.identity.venue,"participants":list(r.identity.event.participants),"start":r.identity.event.scheduled_start_ms,"subject":r.identity.subject,"market":r.venue_market_id,"side":r.side,"key":r.contract_key})
    return {"total":len(rows),"families":dict(fam),"moneyline_examples":money}
async def run_once():
    async with httpx.AsyncClient(timeout=30) as h:
        g,p,k=await asyncio.gather(fetch_gemini_mlb(h),fetch_polymarket_mlb(h),fetch_kalshi_mlb(h))
    gn=normalize_gemini_mlb(g);pn=normalize_polymarket_mlb(p);kn=normalize_kalshi_mlb_eastern(k)
    out={"status":"THREE_VENUE_NORMALIZATION_DIAG","raw":{"gemini":len(g),"polymarket":len(p),"kalshi":len(k)},"gemini":compact(gn),"polymarket":compact(pn),"kalshi":compact(kn),"read_only":True,"order_entry":False}
    print("THREE_VENUE_NORMALIZATION_DIAG "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out
def main(): asyncio.run(run_once())
if __name__=="__main__": main()
