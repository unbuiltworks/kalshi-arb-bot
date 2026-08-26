"""Read-only current SEA/TEN single-leg discovery probe.

Finds current NFL Seattle/Tennessee single markets on Gemini, Polymarket US, and
Kalshi and prints only the matching event/market metadata needed to build the
strict exact-complement scanner. No authentication and no order entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

import httpx

from src.gemini_sports_inventory import fetch_active_sports_events
from src.sports_kp_live_event_inventory import fetch_current_sports_events

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
logger = logging.getLogger("kalshi_arb.sports_single_leg_sea_ten_probe")


def _blob(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, default=str).lower()
    except Exception:
        return str(obj).lower()


def _sea_ten(obj: Any) -> bool:
    text = _blob(obj)
    sea = any(x in text for x in ("seattle", "seahawks", "-sea-", "sea seahawks", "sea"))
    ten = any(x in text for x in ("tennessee", "titans", "-ten-", "ten titans", "ten"))
    # Require stronger full-name/ticker evidence to avoid generic 'sea'/'ten' text.
    strong_sea = any(x in text for x in ("seattle", "seahawks", "-sea-", "sea-ten", "ten-sea", "seat", "seaten"))
    strong_ten = any(x in text for x in ("tennessee", "titans", "-ten-", "sea-ten", "ten-sea", "seaten"))
    return sea and ten and strong_sea and strong_ten


def _trim_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    keep = {
        "id","ticker","event_ticker","series_ticker","slug","title","name","question","description",
        "subtitle","sub_title","yes_sub_title","no_sub_title","marketType","sportsMarketType",
        "sportsMarketTypeV2","line","strike_type","floor_strike","cap_strike","functional_strike",
        "startTime","startDate","endDate","effectiveDate","expiryDate","expected_expiration_time",
        "close_time","expiration_time","rules_primary","rules_secondary","result","status","category",
        "subject","sportsMarket","contracts","markets","outcomes","tokens","clobTokenIds",
        "bestBid","bestAsk","lastTradePrice","volume","volumeNum","liquidity","liquidityNum",
    }
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in keep:
            out[k] = v
    return out


async def _kalshi_series(http: httpx.AsyncClient, series: str) -> list[dict[str, Any]]:
    r = await http.get(
        f"{KALSHI}/events",
        params={"limit":200,"status":"open","series_ticker":series,"with_nested_markets":"true"},
    )
    r.raise_for_status()
    return [dict(x) for x in (r.json().get("events") or []) if isinstance(x, Mapping)]


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_task = fetch_active_sports_events(http)
        poly_task = fetch_current_sports_events(http, horizon_hours=48)
        kalshi_tasks = [_kalshi_series(http, s) for s in ("KXNFLTOTAL","KXNFLSPREAD","KXNFLGAME")]
        results = await asyncio.gather(gemini_task, poly_task, *kalshi_tasks, return_exceptions=True)

    gemini = results[0] if isinstance(results[0], list) else []
    poly = results[1] if isinstance(results[1], list) else []
    kalshi_rows: list[dict[str, Any]] = []
    kalshi_errors: list[str] = []
    for series, result in zip(("KXNFLTOTAL","KXNFLSPREAD","KXNFLGAME"), results[2:]):
        if isinstance(result, Exception):
            kalshi_errors.append(f"{series}:{type(result).__name__}:{result}")
        elif isinstance(result, list):
            kalshi_rows.extend(result)

    g_hits = [_trim_mapping(x) for x in gemini if isinstance(x, Mapping) and _sea_ten(x)]
    p_hits = [_trim_mapping(x) for x in poly if isinstance(x, Mapping) and _sea_ten(x)]
    k_hits = [_trim_mapping(x) for x in kalshi_rows if isinstance(x, Mapping) and _sea_ten(x)]
    payload = {
        "gemini_active_sports_events": len(gemini),
        "polymarket_current_sports_events": len(poly),
        "kalshi_nfl_events_scanned": len(kalshi_rows),
        "gemini_matches": g_hits,
        "polymarket_matches": p_hits,
        "kalshi_matches": k_hits,
        "kalshi_errors": kalshi_errors,
        "classification":"DISCOVERY_ONLY_NOT_RULE_CERTIFIED",
        "order_entry":False,
    }
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    logger.info("SPORTS_SINGLE_LEG_SEA_TEN_PROBE %s", encoded)
    print("SPORTS_SINGLE_LEG_SEA_TEN_PROBE " + encoded, flush=True)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
