"""Read-only NCAAF Gemini/Kalshi identity-shape probe. No auth or order entry."""
from __future__ import annotations
import asyncio, json, logging
from typing import Any, Mapping
import httpx

GEMINI="https://api.gemini.com"
KALSHI="https://external-api.kalshi.com/trade-api/v2"
log=logging.getLogger("kalshi_arb.sports_ncaaf_identity_probe")


def slim_event(e: Mapping[str,Any]) -> dict[str,Any]:
    contracts=e.get("contracts") or []
    return {
        "ticker":e.get("ticker") or e.get("eventTicker"),
        "title":e.get("title") or e.get("name") or e.get("label"),
        "sportsMarket":e.get("sportsMarket"),
        "contract_samples":[{
            "symbol":c.get("instrumentSymbol") or c.get("ticker"),
            "label":c.get("label") or c.get("title"),
            "strike":c.get("strike"),
        } for c in contracts[:4] if isinstance(c,Mapping)],
    }

async def main_async() -> None:
    async with httpx.AsyncClient(timeout=15.0) as http:
        gr=await http.get(f"{GEMINI}/v1/prediction-markets/events",params={"status":"active","sport":"american_football","limit":500,"offset":0}); gr.raise_for_status()
        ge=[x for x in (gr.json().get("data") or gr.json().get("events") or []) if isinstance(x,Mapping)]
        ncaaf=[x for x in ge if "NCAAF" in str(x.get("ticker") or x.get("eventTicker") or "").upper()]
        kr=await http.get(f"{KALSHI}/events",params={"status":"open","series_ticker":"KXNCAAFGAME","limit":200,"with_nested_markets":"true"}); kr.raise_for_status()
        ke=[x for x in (kr.json().get("events") or []) if isinstance(x,Mapping)]
    out={
        "status":"SPORTS_NCAAF_IDENTITY_PROBE",
        "gemini_ncaaf_events":len(ncaaf),
        "gemini_samples":[slim_event(x) for x in ncaaf[:25]],
        "kalshi_game_events":len(ke),
        "kalshi_samples":[{
            "event_ticker":e.get("event_ticker"),"title":e.get("title"),"sub_title":e.get("sub_title"),
            "market_samples":[{"ticker":m.get("ticker"),"title":m.get("title"),"yes_sub_title":m.get("yes_sub_title")} for m in (e.get("markets") or [])[:4] if isinstance(m,Mapping)]
        } for e in ke[:25]],
        "read_only":True,"order_entry":False,
    }
    log.warning("SPORTS_NCAAF_IDENTITY_PROBE %s",json.dumps(out,separators=(",",":"),default=str))

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())
