"""Low-rate read-only Gemini/Kalshi sports inventory monitor.

The broad discovery pass already identified the high-value football surface. Ongoing
production monitoring is now deliberately restricted to NFL/NCAAF game, spread, and
total series so diagnostics do not consume the request budget needed by execution.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import logging
import time
from typing import Any, Mapping

import httpx

logger = logging.getLogger("kalshi_arb.sports_gk_general_inventory")
GEMINI = "https://api.gemini.com"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
SPORT_QUERIES = {"football": "american_football", "baseball": "baseball", "soccer": "soccer", "tennis": "tennis"}
TARGET_SERIES = (
    "KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL",
    "KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL",
)
TTL_SECONDS = 600.0
_cache: dict[str, Any] | None = None
_cache_at = 0.0
_lock = asyncio.Lock()


async def _get(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    last: httpx.Response | None = None
    for attempt in range(5):
        response = await http.get(url, **kwargs); last = response
        if response.status_code != 429:
            response.raise_for_status(); body = response.json() if response.content else {}
            return body if isinstance(body, dict) else {}
        retry_after = response.headers.get("retry-after", "").strip()
        try: header = float(retry_after) if retry_after else 0.0
        except ValueError: header = 0.0
        await asyncio.sleep(max(header, min(0.5 * (2 ** attempt), 4.0)))
    assert last is not None; last.raise_for_status(); return {}


async def _gemini(http: httpx.AsyncClient) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for canonical, query_sport in SPORT_QUERIES.items():
        try:
            body = await _get(http, f"{GEMINI}/v1/prediction-markets/events", params={
                "status": "active", "sport": query_sport, "limit": 500, "offset": 0,
            })
            rows = [dict(x) for x in (body.get("data") or body.get("events") or []) if isinstance(x, Mapping)]
            league_counts = Counter()
            contract_count = 0
            for row in rows:
                ticker = str(row.get("ticker") or row.get("eventTicker") or "")
                league = ticker.split("-", 1)[0].upper() if "-" in ticker else "UNKNOWN"
                league_counts[league] += 1
                contract_count += len(row.get("contracts") or [])
            out[canonical] = {
                "query_sport": query_sport, "events": len(rows), "contracts": contract_count,
                "leagues": dict(league_counts.most_common(20)),
            }
        except Exception as exc:
            out[canonical] = {"query_sport": query_sport, "events": None, "error": type(exc).__name__}
        await asyncio.sleep(0.05)
    return out


async def _kalshi(http: httpx.AsyncClient) -> dict[str, Any]:
    active: list[dict[str, Any]] = []
    failures = 0
    for series in TARGET_SERIES:
        try:
            body = await _get(http, f"{KALSHI}/events", params={
                "limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true",
            })
            events = [dict(x) for x in (body.get("events") or []) if isinstance(x, Mapping)]
            active.append({
                "ticker": series, "open_events": len(events),
                "open_markets": sum(len(e.get("markets") or []) for e in events),
            })
        except Exception:
            failures += 1
        await asyncio.sleep(0.30)
    return {"target_series": active, "series_probe_failures": failures}


async def _scan() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as http:
        gemini, kalshi = await asyncio.gather(_gemini(http), _kalshi(http))
    return {
        "status": "SPORTS_GK_GENERAL_INVENTORY", "gemini": gemini,
        "gemini_event_counts": {k: v.get("events") for k, v in gemini.items()},
        "kalshi": kalshi, "read_only": True, "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and now - _cache_at < TTL_SECONDS:
        out = dict(_cache); out["cached"] = True; return out
    async with _lock:
        now = time.monotonic()
        if _cache is None or now - _cache_at >= TTL_SECONDS:
            _cache = await _scan(); _cache_at = time.monotonic()
        out = dict(_cache); out["cached"] = False
    logger.warning("SPORTS_GK_GENERAL_INVENTORY %s", json.dumps(out, separators=(",", ":"), default=str))
    return out
