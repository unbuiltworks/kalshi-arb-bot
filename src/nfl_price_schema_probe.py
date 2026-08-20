"""Read-only NFL quote-shape probe for shadow pricing implementation."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping

import httpx

GEMINI="https://api.gemini.com/v1/prediction-markets/events"
KALSHI="https://external-api.kalshi.com/trade-api/v2/events"
ODDS="https://api.the-odds-api.com/v4/sports/americanfootball_nfl_preseason/odds"


async def run() -> dict[str, Any]:
    key=os.getenv("THE_ODDS_API_KEY","").strip()
    if not key: raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=20.0) as http:
        g=await http.get(GEMINI,params={"status":"active","sport":"american_football","sports_market_type":"moneyline","limit":100,"offset":0})
        g.raise_for_status(); gb=g.json(); gevents=[x for x in gb.get("data") or gb.get("events") or [] if isinstance(x,Mapping)]
        gevent=next(x for x in gevents if "NFL" in str(x.get("ticker") or "").upper())
        gc=next(x for x in gevent.get("contracts") or [] if isinstance(x,Mapping))

        k=await http.get(KALSHI,params={"limit":10,"status":"open","series_ticker":"KXNFLGAME","with_nested_markets":"true"})
        k.raise_for_status(); kb=k.json(); kevent=next(x for x in kb.get("events") or [] if isinstance(x,Mapping)); km=next(x for x in kevent.get("markets") or [] if isinstance(x,Mapping))

        o=await http.get(ODDS,params={"apiKey":key,"regions":"us","markets":"h2h,spreads,totals","oddsFormat":"decimal","dateFormat":"iso"})
        o.raise_for_status(); ob=o.json(); oe=next(x for x in ob if isinstance(x,Mapping)); book=next(x for x in oe.get("bookmakers") or [] if isinstance(x,Mapping)); market=next(x for x in book.get("markets") or [] if isinstance(x,Mapping)); outcome=next(x for x in market.get("outcomes") or [] if isinstance(x,Mapping))

    return {
        "status":"NFL_PRICE_SCHEMA_PROBE","read_only":True,"order_entry":False,
        "gemini":{
            "event_ticker":gevent.get("ticker"),"instrumentSymbol":gc.get("instrumentSymbol"),
            "prices":gc.get("prices"),"marketState":gc.get("marketState"),
            "quantityMinimum":gc.get("quantityMinimum"),"priceIncrement":gc.get("priceIncrement"),
        },
        "kalshi":{
            "event_ticker":kevent.get("event_ticker"),"ticker":km.get("ticker"),
            "yes_bid":km.get("yes_bid"),"yes_ask":km.get("yes_ask"),"no_bid":km.get("no_bid"),"no_ask":km.get("no_ask"),
            "yes_bid_dollars":km.get("yes_bid_dollars"),"yes_ask_dollars":km.get("yes_ask_dollars"),
            "no_bid_dollars":km.get("no_bid_dollars"),"no_ask_dollars":km.get("no_ask_dollars"),
        },
        "sportsbook":{
            "book":book.get("key"),"market":market.get("key"),"outcome":dict(outcome),
            "quota":{"remaining":o.headers.get("x-requests-remaining",""),"last_cost":o.headers.get("x-requests-last","")},
        },
    }


def main()->None:
    print(json.dumps(asyncio.run(run()),separators=(",",":"),sort_keys=True,default=str),flush=True)


if __name__=="__main__": main()
