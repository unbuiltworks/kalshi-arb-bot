"""One-shot exact current FanDuel-vs-prediction MLB full-game raw-lock dump.

This uses the same v2 scope gates as production: Polymarket first-five events are
removed before pricing and direct market IDs receive a second full-game guard.
Output remains normal-event economics only; no rule promotion and no order entry.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx

import src.the_odds_api_mlb_shadow_v2 as base


async def main() -> None:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")

    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_result, gemini, poly_raw, kalshi = await asyncio.gather(
            base.fetch_sportsbooks(http, api_key),
            base.fetch_gemini_mlb(http),
            base.fetch_polymarket_mlb(http),
            base.fetch_kalshi_mlb(http),
        )
        events, quota = sportsbook_result
        poly = base.full_game_only_events(poly_raw)
        direct, errors, direct_keys = await base.price_counterparts(http, gemini, poly, kalshi)

    received_ms = int(time.time() * 1000)
    retail = base.sportsbook_quotes(events, received_ms=received_ms)
    direct_quotes = [
        row.quote for row in direct
        if base._is_full_game_direct_market(row.quote.market_id)
    ]
    direct_key_set = {quote.settlement_key for quote in direct_quotes}
    fanduel = [
        quote for quote in retail
        if quote.venue == "sportsbook:fanduel" and quote.settlement_key in direct_key_set
    ]
    locks = base._dedupe(base.find_cross_venue_locks(
        direct_quotes + fanduel,
        max_quote_skew_ms=70_000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    locks = [
        row for row in locks
        if base._is_retail(row.yes_venue) != base._is_retail(row.no_venue)
        and (row.yes_venue == "sportsbook:fanduel" or row.no_venue == "sportsbook:fanduel")
    ]
    print(json.dumps({
        "status": "FANDUEL_MLB_CURRENT_LOCKS_PROBE_V2_FULL_GAME_ONLY",
        "sportsbook_events": len(events),
        "direct_counterpart_keys": direct_keys,
        "direct_priced_quotes": len(direct_quotes),
        "fanduel_matching_quotes": len(fanduel),
        "fanduel_positive_locks": len(locks),
        "locks": [row.as_dict() for row in locks],
        "quota": quota,
        "fetch_error_count": len(errors),
        "scope_gate": "FULL_GAME_ONLY_HARD_FILTER",
        "classification": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
