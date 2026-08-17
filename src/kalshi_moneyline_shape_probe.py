"""Public read-only diagnostic for Kalshi MLB moneyline market side semantics."""
from __future__ import annotations
import asyncio,json
import httpx

TICKERS=("KXMLBGAME-26AUG171805BALTB-BAL","KXMLBGAME-26AUG171805BALTB-TB")

async def main_async():
    out=[]
    async with httpx.AsyncClient(timeout=30.0) as http:
        for ticker in TICKERS:
            r=await http.get(f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}")
            r.raise_for_status(); m=(r.json().get("market") or {})
            out.append({k:m.get(k) for k in ("ticker","event_ticker","title","subtitle","yes_sub_title","no_sub_title","yes_bid","yes_ask","no_bid","no_ask","expected_expiration_time","close_time")})
    print(json.dumps(out,separators=(",",":"),default=str))

def main(): asyncio.run(main_async())
if __name__=="__main__": main()
