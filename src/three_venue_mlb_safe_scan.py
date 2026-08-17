"""Scope-safe public MLB three-venue scan: identity + fee-inclusive q=1 pricing."""
from __future__ import annotations
import asyncio, json
from collections import Counter
import httpx

from src.polymarket_scope_guard import full_game_only_events
from src.three_venue_mlb_price_scan import _dedupe, price_counterparts
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb, summarize

async def run_once():
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly_raw, kalshi = await asyncio.gather(fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        poly = full_game_only_events(poly_raw)
        identity = summarize(gemini, poly, kalshi)
        priced, errors, key_count = await price_counterparts(http, gemini, poly, kalshi)
    locks = _dedupe(find_cross_venue_locks(
        [row.quote for row in priced], max_quote_skew_ms=5000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    return {
        "status":"THREE_VENUE_MLB_SCOPE_SAFE_SCAN",
        "raw_events":identity["raw_events"],
        "normalized_sides":identity["normalized_sides"],
        "counterpart_clusters":identity["counterpart_clusters"],
        "three_venue_clusters":identity["three_venue_clusters"],
        "clusters_by_family":identity["clusters_by_family"],
        "clusters_by_venue_set":identity["clusters_by_venue_set"],
        "normal_event_counterpart_keys":key_count,
        "priced_quotes":len(priced),
        "priced_quotes_by_venue":dict(Counter(row.quote.venue for row in priced)),
        "one_contract_positive_locks":len(locks),
        "top_one_contract_locks":[row.as_dict() for row in locks[:50]],
        "fetch_error_count":len(errors),
        "scope_gate":"POLYMARKET_MAIN_FULL_GAME_ONLY",
        "rule_gate":"FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible":False,
        "read_only":True,
        "order_entry":False,
    }

def main(): print(json.dumps(asyncio.run(run_once()),sort_keys=True,default=str))
if __name__=="__main__": main()
