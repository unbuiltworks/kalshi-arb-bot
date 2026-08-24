"""Targeted public refdata probe for current Gemini/Kalshi 15m pairs.

Avoids broad Kalshi catalog paging. Reports the instrument-specific Gemini quantity/
price increments and the exact Kalshi market fractional-trading/price-grid flags that
must be known before a resting q1 maker can be promoted.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_kalshi_live_shadow import KALSHI_REST

logger = logging.getLogger("kalshi_arb.gemini_kalshi_current_refdata_probe")
TARGET_ASSETS = {"ETH", "SOL", "XRP"}


async def _kalshi_market(http: httpx.AsyncClient, ticker: str) -> dict[str, Any]:
    for attempt in range(6):
        r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
        if r.status_code != 429:
            r.raise_for_status()
            body = r.json()
            row = body.get("market") if isinstance(body, Mapping) else None
            return dict(row) if isinstance(row, Mapping) else dict(body) if isinstance(body, Mapping) else {}
        await asyncio.sleep(min(2.0, 0.20 * (2**attempt)))
    return {}


def _contract(events: list[dict[str, Any]], symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        for raw in event.get("contracts") or []:
            if isinstance(raw, Mapping) and str(raw.get("instrumentSymbol") or "") == symbol:
                return dict(event), dict(raw)
    return {}, {}


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        pairs = await _discover_pairs(http, 30.0)
        events = await _gemini_active_crypto(http)
        rows=[]
        now_pairs={}
        for pair in pairs:
            if pair.asset in TARGET_ASSETS:
                prior=now_pairs.get(pair.asset)
                if prior is None or pair.window_end < prior.window_end:
                    now_pairs[pair.asset]=pair
        for asset,pair in sorted(now_pairs.items()):
            event,contract=_contract(events,pair.gemini_symbol)
            kalshi=await _kalshi_market(http,pair.kalshi_ticker)
            rows.append({
                "asset":asset,
                "window_end_utc":pair.window_end.isoformat(),
                "gemini_event_ticker":event.get("ticker"),
                "gemini_symbol":pair.gemini_symbol,
                "gemini_market_state":contract.get("marketState"),
                "gemini_quantity_increment":contract.get("quantityIncrement"),
                "gemini_quantity_minimum":contract.get("quantityMinimum"),
                "gemini_price_increment":contract.get("priceIncrement"),
                "gemini_price_minimum":contract.get("priceMinimum"),
                "gemini_strike":(contract.get("strike") or {}).get("value") if isinstance(contract.get("strike"),Mapping) else None,
                "kalshi_ticker":pair.kalshi_ticker,
                "kalshi_status":kalshi.get("status"),
                "kalshi_fractional_trading_enabled":kalshi.get("fractional_trading_enabled"),
                "kalshi_price_level_structure":kalshi.get("price_level_structure"),
                "kalshi_price_ranges":kalshi.get("price_ranges"),
                "kalshi_yes_bid":kalshi.get("yes_bid_dollars"),
                "kalshi_yes_ask":kalshi.get("yes_ask_dollars"),
                "order_entry":False,
            })
    out={"pairs":rows,"pair_count":len(rows),"read_only":True,"order_entry":False,"realized":False}
    logger.warning("CRYPTO_CURRENT_REFDATA %s",json.dumps(out,separators=(",",":"),default=str))
    return out


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())

if __name__=="__main__": main()
