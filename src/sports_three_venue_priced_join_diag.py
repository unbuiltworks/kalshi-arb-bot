"""Read-only diagnostic for priced quote to normalized-side join."""
from __future__ import annotations
import asyncio,json
from collections import Counter
import httpx
from src.mlb_game_side_normalization import normalize_gemini_mlb,normalize_polymarket_mlb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.three_venue_mlb_snapshot_pricing import price_counterparts_snapshot
from src.universal_three_venue_sports import fetch_gemini_mlb,fetch_polymarket_mlb,fetch_kalshi_mlb

def key(s): return (s.identity.venue,f"{s.venue_market_id}#{s.side}")
async def run_once():
    async with httpx.AsyncClient(timeout=30) as h:
        g,p,k=await asyncio.gather(fetch_gemini_mlb(h),fetch_polymarket_mlb(h),fetch_kalshi_mlb(h))
        priced,errors,nkeys=await price_counterparts_snapshot(h,g,p,k)
    sides=normalize_gemini_mlb(g)+normalize_polymarket_mlb(p)+normalize_kalshi_mlb_eastern(k)
    lookup={key(s):s for s in sides}
    matched=[]; misses=[]
    for pr in priced:
        q=pr.quote
        s=lookup.get((q.venue,q.market_id))
        if s is None:
            if len(misses)<30: misses.append({"venue":q.venue,"market_id":q.market_id,"settlement_key":q.settlement_key})
        else:
            matched.append(s)
    out={"status":"THREE_VENUE_PRICED_JOIN_DIAG","priced":len(priced),"priced_by_venue":dict(Counter(x.quote.venue for x in priced)),"matched":len(matched),"matched_families":dict(Counter(s.identity.market_family for s in matched)),"moneyline_matched":sum(s.identity.market_family=="moneyline" for s in matched),"miss_count":len(priced)-len(matched),"miss_examples":misses,"priced_examples":[x.as_dict() for x in priced[:15]],"error_count":len(errors),"errors":errors[:10],"counterpart_keys":nkeys,"read_only":True,"order_entry":False}
    print("THREE_VENUE_PRICED_JOIN_DIAG "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out
def main(): asyncio.run(run_once())
if __name__=="__main__": main()
