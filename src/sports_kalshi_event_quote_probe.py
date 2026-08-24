"""One-request read-only probe for quote fields in Kalshi nested sports events."""
from __future__ import annotations
import asyncio, json, logging
import httpx

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
log = logging.getLogger("kalshi_arb.sports_kalshi_event_quote_probe")

async def main_async() -> None:
    async with httpx.AsyncClient(timeout=12.0) as http:
        r = await http.get(f"{KALSHI}/events", params={"limit": 1, "status": "open", "series_ticker": "KXNFLSPREAD", "with_nested_markets": "true"})
        r.raise_for_status()
        events = r.json().get("events") or []
        market = ((events[0].get("markets") or [])[0] if events and (events[0].get("markets") or []) else {})
        quoteish = {k:v for k,v in market.items() if any(x in k.lower() for x in ("bid","ask","price","volume","liquidity","count"))}
        log.warning("KALSHI_NESTED_QUOTE_PROBE status=%s market_keys=%s quote_fields=%s order_entry=false", r.status_code, sorted(market.keys()), json.dumps(quoteish, separators=(",",":"), default=str))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())
