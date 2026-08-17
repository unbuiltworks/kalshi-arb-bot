"""Read-only The Odds API MLB shadow scanner with hard full-game scope.

This wrapper intentionally reuses the original sportsbook adapter, but filters
Polymarket to full-game events before pricing so first-five-inning markets can
never match full-game sportsbook lines. No order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import logging
import os
import time
from typing import Any, Mapping

import httpx

from src.polymarket_scope_guard import full_game_only_events
from src.the_odds_api_mlb_shadow import fetch_sportsbooks, sportsbook_quotes
from src.three_venue_mlb_price_scan import _dedupe, price_counterparts
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.the_odds_api_mlb_shadow_v2")


def _is_retail(venue: str) -> bool:
    return str(venue).startswith("sportsbook:")


def _is_full_game_direct_market(market_id: str) -> bool:
    text = str(market_id).lower()
    return not any(token in text for token in ("-f5-", ":f5:", "first-5", "first5", "first five"))


async def run_once(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_result, gemini, poly_raw, kalshi = await asyncio.gather(
            fetch_sportsbooks(http, api_key),
            fetch_gemini_mlb(http),
            fetch_polymarket_mlb(http),
            fetch_kalshi_mlb(http),
        )
        events, quota = sportsbook_result
        poly = full_game_only_events(poly_raw)
        direct, errors, direct_keys = await price_counterparts(http, gemini, poly, kalshi)

    received_ms = int(time.time() * 1000)
    retail = sportsbook_quotes(events, received_ms=received_ms)
    direct_quotes = [row.quote for row in direct if _is_full_game_direct_market(row.quote.market_id)]
    direct_key_set = {q.settlement_key for q in direct_quotes}
    matched_retail = [q for q in retail if q.settlement_key in direct_key_set]

    locks = _dedupe(find_cross_venue_locks(
        direct_quotes + matched_retail,
        max_quote_skew_ms=70000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    retail_vs_prediction = [
        x for x in locks
        if _is_retail(x.yes_venue) != _is_retail(x.no_venue)
    ]

    result = {
        "status": "THE_ODDS_API_MLB_SHADOW_V2_FULL_GAME_ONLY",
        "sportsbook_events": len(events),
        "sportsbook_quotes": len(retail),
        "sportsbook_quotes_matching_direct_keys": len(matched_retail),
        "books": dict(Counter(q.venue for q in retail)),
        "direct_counterpart_keys": direct_keys,
        "direct_priced_quotes": len(direct_quotes),
        "retail_vs_prediction_positive_locks": len(retail_vs_prediction),
        "top_retail_vs_prediction_locks": [x.as_dict() for x in retail_vs_prediction[:30]],
        "quota": quota,
        "fetch_error_count": len(errors),
        "scope_gate": "FULL_GAME_ONLY_HARD_FILTER",
        "rule_gate": "SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("THE_ODDS_API_MLB_SHADOW_V2 %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def _remaining(result: Mapping[str, Any]) -> int | None:
    quota = result.get("quota") if isinstance(result.get("quota"), Mapping) else {}
    try:
        return int(str(quota.get("remaining")))
    except (TypeError, ValueError):
        return None


async def supervise(api_key: str, interval_s: float, quota_floor: int) -> None:
    while True:
        started = time.monotonic()
        try:
            result = await run_once(api_key)
            remaining = _remaining(result)
            if remaining is not None and remaining <= quota_floor:
                logger.warning(
                    "THE_ODDS_API_QUOTA_GUARD remaining=%s floor=%s polling_paused=true",
                    remaining, quota_floor,
                )
                while True:
                    await asyncio.sleep(3600.0)
        except Exception:
            logger.exception("THE_ODDS_API_MLB_SHADOW_V2_ERROR")
        await asyncio.sleep(max(5.0, interval_s - (time.monotonic() - started)))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--quota-floor", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    if args.once:
        print(json.dumps(asyncio.run(run_once(api_key)), sort_keys=True, default=str))
    else:
        asyncio.run(supervise(api_key, args.interval_seconds, max(0, args.quota_floor)))


if __name__ == "__main__":
    main()
