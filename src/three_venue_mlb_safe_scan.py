"""Scope-safe public MLB three-venue scan: identity + fee-inclusive q=1 pricing."""
from __future__ import annotations
import asyncio, json
from collections import Counter
import httpx

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import normalize_gemini_mlb, normalize_polymarket_mlb
from src.polymarket_scope_guard import full_game_only_events
from src.three_venue_mlb_price_scan import _dedupe
from src.three_venue_mlb_snapshot_pricing import price_counterparts_snapshot
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import counterpart_clusters, fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

async def run_once():
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly_raw, kalshi = await asyncio.gather(fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        poly = full_game_only_events(poly_raw)
        g=normalize_gemini_mlb(gemini); p=normalize_polymarket_mlb(poly); k=normalize_kalshi_mlb_eastern(kalshi)
        clusters=counterpart_clusters(g+p+k)
        priced, errors, key_count = await price_counterparts_snapshot(http, gemini, poly, kalshi)
    locks = _dedupe(find_cross_venue_locks([row.quote for row in priced], max_quote_skew_ms=5000, min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"))
    by_family=Counter(row["market_family"] for row in clusters)
    by_venues=Counter("+".join(row["venues"]) for row in clusters)
    positive_keys={row.settlement_key for row in locks}
    positive_quotes=[row.as_dict() for row in priced if row.quote.settlement_key in positive_keys]
    positive_clusters=[row for row in clusters if row["contract_key"] in positive_keys]
    return {
        "status":"THREE_VENUE_MLB_SCOPE_SAFE_SCAN",
        "raw_events":{"gemini":len(gemini),"polymarket":len(poly_raw),"kalshi":len(kalshi)},
        "normalized_sides":{"gemini":len(g),"polymarket":len(p),"kalshi":len(k)},
        "counterpart_clusters":len(clusters),
        "three_venue_clusters":sum(row["venue_count"]==3 for row in clusters),
        "clusters_by_family":dict(by_family),
        "clusters_by_venue_set":dict(by_venues),
        "normal_event_counterpart_keys":key_count,
        "priced_quotes":len(priced),
        "priced_quotes_by_venue":dict(Counter(row.quote.venue for row in priced)),
        "one_contract_positive_locks":len(locks),
        "top_one_contract_locks":[row.as_dict() for row in locks[:50]],
        "positive_quote_audit":positive_quotes,
        "positive_identity_audit":positive_clusters,
        "fetch_error_count":len(errors),
        "fetch_errors":errors[:20],
        "scope_gate":"POLYMARKET_MAIN_FULL_GAME_ONLY",
        "time_gate":"KALSHI_TICKER_AMERICA_NEW_YORK_CORROBORATED_BY_EXPECTED_EXPIRY",
        "kalshi_price_source":"NESTED_EVENT_MARKET_TOP_OF_BOOK",
        "rule_gate":"FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible":False,"read_only":True,"order_entry":False,
    }

def main(): print(json.dumps(asyncio.run(run_once()),sort_keys=True,default=str))
if __name__=="__main__": main()
