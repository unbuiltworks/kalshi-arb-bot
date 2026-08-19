"""One-shot read-only dump of current FanDuel-vs-prediction MLB raw locks.

Uses exactly the same normalization, executable q=1 prediction pricing, and Odds API
sportsbook odds conversion as the production shadow. This probe deliberately skips
rule promotion and only exposes normal-event economic candidates so the next rule
audit can target the family that is actually live now.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx

from src.the_odds_api_mlb_shadow import fetch_sportsbooks, sportsbook_quotes
from src.three_venue_mlb_price_scan import _dedupe, price_counterparts
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb


async def main() -> None:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_result, gemini, poly, kalshi = await asyncio.gather(
            fetch_sportsbooks(http, api_key),
            fetch_gemini_mlb(http),
            fetch_polymarket_mlb(http),
            fetch_kalshi_mlb(http),
        )
        events, quota = sportsbook_result
        direct, errors, direct_keys = await price_counterparts(http, gemini, list(poly), kalshi)

    received_ms = int(time.time() * 1000)
    retail = sportsbook_quotes(events, received_ms=received_ms)
    direct_quotes = [row.quote for row in direct]
    direct_key_set = {quote.settlement_key for quote in direct_quotes}
    fanduel = [
        quote for quote in retail
        if quote.venue == "sportsbook:fanduel" and quote.settlement_key in direct_key_set
    ]
    locks = _dedupe(find_cross_venue_locks(
        direct_quotes + fanduel,
        max_quote_skew_ms=70_000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    locks = [
        row for row in locks
        if row.yes_venue == "sportsbook:fanduel" or row.no_venue == "sportsbook:fanduel"
    ]
    print(json.dumps({
        "status": "FANDUEL_MLB_CURRENT_LOCKS_PROBE",
        "sportsbook_events": len(events),
        "direct_counterpart_keys": direct_keys,
        "fanduel_matching_quotes": len(fanduel),
        "fanduel_positive_locks": len(locks),
        "locks": [row.as_dict() for row in locks],
        "quota": quota,
        "fetch_error_count": len(errors),
        "classification": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
