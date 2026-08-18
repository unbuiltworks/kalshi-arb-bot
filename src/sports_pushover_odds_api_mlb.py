"""The Odds API MLB shadow + read-only qualified Pushover alerts.

This replaces no pricing logic and performs no order entry. It reuses the exact
full-game-only v2 scanner primitives, then routes qualified retail-vs-prediction
locks through sports_pushover_alerts.
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

import src.the_odds_api_mlb_shadow_v2 as base
from src.sports_pushover_alerts import SportsPushoverAlerts

logger = logging.getLogger("kalshi_arb.sports_pushover_odds_api_mlb")
_ALERTS = SportsPushoverAlerts()


async def run_once(api_key: str) -> dict[str, Any]:
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
    direct_quotes = [row.quote for row in direct if base._is_full_game_direct_market(row.quote.market_id)]
    direct_key_set = {q.settlement_key for q in direct_quotes}
    matched_retail = [q for q in retail if q.settlement_key in direct_key_set]

    locks = base._dedupe(base.find_cross_venue_locks(
        direct_quotes + matched_retail,
        max_quote_skew_ms=70000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    retail_vs_prediction = [
        row for row in locks
        if base._is_retail(row.yes_venue) != base._is_retail(row.no_venue)
    ]
    tally = base._update_tally(retail_vs_prediction)

    alert_rows: list[Any] = [*direct, *matched_retail]
    alerts_sent = 0
    alert_errors = 0
    for lock in retail_vs_prediction:
        try:
            if await _ALERTS.maybe_send(
                lock,
                alert_rows,
                rule_status="FULL_GAME_ONLY; SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
                source="odds-api-mlb",
            ):
                alerts_sent += 1
        except Exception as exc:
            alert_errors += 1
            logger.warning("SPORTS_PUSHOVER_ERROR type=%s detail=%s", type(exc).__name__, str(exc)[:240])

    result = {
        "status": "THE_ODDS_API_MLB_SHADOW_V2_FULL_GAME_ONLY_PUSHOVER",
        "sportsbook_events": len(events),
        "sportsbook_quotes": len(retail),
        "sportsbook_quotes_matching_direct_keys": len(matched_retail),
        "books": dict(Counter(q.venue for q in retail)),
        "direct_counterpart_keys": direct_keys,
        "direct_priced_quotes": len(direct_quotes),
        "retail_vs_prediction_positive_locks": len(retail_vs_prediction),
        "top_retail_vs_prediction_locks": [x.as_dict() for x in retail_vs_prediction[:30]],
        "sportsbook_opportunity_tally": tally,
        "pushover": {
            "configured": _ALERTS.configured,
            "alerts_sent_this_scan": alerts_sent,
            "alert_errors_this_scan": alert_errors,
            "qualification": "positive_after_fee + freshness/skew + material_realert_only",
        },
        "quota": quota,
        "fetch_error_count": len(errors),
        "scope_gate": "FULL_GAME_ONLY_HARD_FILTER",
        "rule_gate": "SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("THE_ODDS_API_MLB_PUSHOVER %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


async def _send_startup_test() -> None:
    if os.getenv("SPORTS_PUSHOVER_TEST_ON_START", "NO").upper() != "YES":
        return
    request_id = await _ALERTS.send(
        title="Sports alerts connected",
        message="Pushover test succeeded. Sports scanners remain read-only; no live order entry is enabled.",
    )
    logger.info("PUSHOVER_TEST_OK request_id=%s", request_id)


def _remaining(result: Mapping[str, Any]) -> int | None:
    quota = result.get("quota") if isinstance(result.get("quota"), Mapping) else {}
    try:
        return int(str(quota.get("remaining")))
    except (TypeError, ValueError):
        return None


async def supervise(api_key: str, interval_s: float, quota_floor: int) -> None:
    await _send_startup_test()
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
            logger.exception("THE_ODDS_API_MLB_PUSHOVER_ERROR")
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
