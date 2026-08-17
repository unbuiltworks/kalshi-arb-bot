"""Public read-only diagnostic for Kalshi MLB schedule timestamps."""
from __future__ import annotations
import asyncio, json
import httpx

SERIES=("KXMLBGAME","KXMLBSPREAD","KXMLBTOTAL")

async def main_async():
    out=[]
    async with httpx.AsyncClient(timeout=30.0) as http:
        for series in SERIES:
            r=await http.get("https://external-api.kalshi.com/trade-api/v2/events",params={"limit":50,"status":"open","series_ticker":series,"with_nested_markets":"true"})
            r.raise_for_status()
            for event in (r.json().get("events") or [])[:8]:
                markets=event.get("markets") or []
                m=markets[0] if markets else {}
                out.append({
                    "series":series,"event_ticker":event.get("event_ticker"),"title":event.get("title"),
                    "strike_date":event.get("strike_date"),"expected_expiration_time":m.get("expected_expiration_time"),
                    "latest_expiration_time":m.get("latest_expiration_time"),"close_time":m.get("close_time"),
                    "open_time":m.get("open_time"),"market_ticker":m.get("ticker")
                })
    print(json.dumps(out[:24],separators=(",",":"),default=str))

def main(): asyncio.run(main_async())
if __name__=="__main__": main()
