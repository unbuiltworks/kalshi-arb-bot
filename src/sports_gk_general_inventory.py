"""Read-only inventory probe for active Gemini/Kalshi sports beyond MLB.

This module is deliberately non-mutating. It is used only while the MLB live executor
has no eligible inventory, so we can see which football/basketball/hockey/etc. series
actually exist before building or arming another execution family.
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
# Gemini's API taxonomy uses american_football while our canonical sport is football.
SPORT_QUERIES = {
    "football": "american_football",
    "basketball": "basketball",
    "hockey": "hockey",
    "soccer": "soccer",
    "tennis": "tennis",
    "baseball": "baseball",
}
SERIES_KEYWORDS = (
    "nfl", "football", "nba", "basketball", "nhl", "hockey", "college football",
    "pro football", "pro basketball", "pro hockey",
)
TTL_SECONDS = 300.0
MAX_SERIES_EVENT_PROBES = 30
_cache: dict[str, Any] | None = None
_cache_at = 0.0
_lock = asyncio.Lock()


def _text(row: Mapping[str, Any]) -> str:
    tags = row.get("tags") or []
    product = row.get("product_metadata") or {}
    return " ".join([
        str(row.get("ticker") or ""), str(row.get("title") or ""),
        str(row.get("category") or ""), " ".join(str(x) for x in tags if x),
        json.dumps(product, sort_keys=True, default=str),
    ]).lower()


def _volume(row: Mapping[str, Any]) -> float:
    for key in ("volume_fp", "volume"):
        try:
            return float(row.get(key) or 0)
        except Exception:
            pass
    return 0.0


async def _get(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    last: httpx.Response | None = None
    for attempt in range(5):
        response = await http.get(url, **kwargs)
        last = response
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json() if response.content else {}
            return body if isinstance(body, dict) else {}
        retry_after = response.headers.get("retry-after", "").strip()
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        await asyncio.sleep(max(delay, min(0.5 * (2 ** attempt), 4.0)))
    assert last is not None
    last.raise_for_status()
    return {}


async def _gemini(http: httpx.AsyncClient) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for canonical, query_sport in SPORT_QUERIES.items():
        try:
            body = await _get(
                http, f"{GEMINI}/v1/prediction-markets/events",
                params={"status": "active", "sport": query_sport, "limit": 500, "offset": 0},
            )
            rows = [dict(x) for x in (body.get("data") or body.get("events") or []) if isinstance(x, Mapping)]
            samples = []
            for row in rows[:12]:
                contracts = [x for x in (row.get("contracts") or []) if isinstance(x, Mapping)]
                samples.append({
                    "event_id": row.get("event_id") or row.get("id"),
                    "ticker": row.get("ticker") or row.get("eventTicker"),
                    "title": row.get("title") or row.get("name"),
                    "start_time": row.get("start_time") or row.get("startTime") or row.get("event_time"),
                    "contract_count": len(contracts),
                    "contract_samples": [
                        {
                            "instrumentSymbol": c.get("instrumentSymbol") or c.get("ticker"),
                            "label": c.get("label") or c.get("abbreviatedName"),
                            "strike": c.get("strike"),
                            "buyYesPrice": c.get("buyYesPrice"),
                            "buyNoPrice": c.get("buyNoPrice"),
                        }
                        for c in contracts[:4]
                    ],
                })
            out[canonical] = {"query_sport": query_sport, "events": len(rows), "samples": samples}
        except Exception as exc:
            out[canonical] = {"query_sport": query_sport, "events": None, "error": type(exc).__name__}
        await asyncio.sleep(0.05)
    return out


async def _kalshi(http: httpx.AsyncClient) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    try:
        filters = await _get(http, f"{KALSHI}/search/filters_by_sport")
    except Exception as exc:
        filters = {"error": type(exc).__name__}

    body = await _get(http, f"{KALSHI}/series", params={
        "category": "Sports", "include_product_metadata": "true", "include_volume": "true",
    })
    series = [dict(x) for x in (body.get("series") or []) if isinstance(x, Mapping)]
    relevant = [row for row in series if any(k in _text(row) for k in SERIES_KEYWORDS)]
    relevant.sort(key=lambda row: (-_volume(row), str(row.get("ticker") or "")))

    active: list[dict[str, Any]] = []
    failures = 0
    for row in relevant[:MAX_SERIES_EVENT_PROBES]:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        try:
            events_body = await _get(http, f"{KALSHI}/events", params={
                "limit": 200, "status": "open", "series_ticker": ticker, "with_nested_markets": "true",
            })
            events = [dict(x) for x in (events_body.get("events") or []) if isinstance(x, Mapping)]
            market_count = sum(len(x.get("markets") or []) for x in events)
            if events:
                active.append({
                    "ticker": ticker, "title": row.get("title"), "tags": row.get("tags") or [],
                    "volume": _volume(row), "open_events": len(events), "open_markets": market_count,
                    "sample_events": [{
                        "event_ticker": e.get("event_ticker"), "title": e.get("title"),
                        "sub_title": e.get("sub_title"), "markets": len(e.get("markets") or []),
                    } for e in events[:5]],
                })
        except Exception:
            failures += 1
        await asyncio.sleep(0.12)

    filter_map = filters.get("filters_by_sports") if isinstance(filters, Mapping) else None
    ordering = filters.get("sport_ordering") if isinstance(filters, Mapping) else None
    return {
        "series_total": len(series), "relevant_series": len(relevant),
        "active_relevant_series": len(active), "active": active[:30],
        "series_probe_failures": failures, "sport_ordering": ordering,
        "filter_sports": sorted(filter_map) if isinstance(filter_map, Mapping) else None,
        "relevant_series_sample": [{
            "ticker": row.get("ticker"), "title": row.get("title"), "tags": row.get("tags") or [],
            "volume": _volume(row), "product_metadata": row.get("product_metadata") or {},
        } for row in relevant[:30]],
    }


async def _scan() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as http:
        gemini, kalshi = await asyncio.gather(_gemini(http), _kalshi(http))
    counts = Counter()
    for sport, row in gemini.items():
        if isinstance(row, Mapping) and isinstance(row.get("events"), int):
            counts[sport] = int(row["events"])
    return {
        "status": "SPORTS_GK_GENERAL_INVENTORY", "gemini": gemini,
        "gemini_event_counts": dict(counts), "kalshi": kalshi,
        "read_only": True, "order_entry": False,
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
