"""Small read-only shape diagnostic for Gemini/Kalshi NFL game lines.

No pricing and no order entry. Fetches only the three NFL line series sequentially
and emits the exact fields needed to build a strict normalizer without guessing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

import httpx

logger = logging.getLogger("kalshi_arb.sports_nfl_gk_diagnostic")
GEMINI = "https://api.gemini.com"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")


async def _get(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    last: httpx.Response | None = None
    for attempt in range(5):
        r = await http.get(url, **kwargs); last = r
        if r.status_code != 429:
            r.raise_for_status(); body = r.json() if r.content else {}
            return body if isinstance(body, dict) else {}
        await asyncio.sleep(min(0.5 * (2 ** attempt), 4.0))
    assert last is not None; last.raise_for_status(); return {}


def _gemini_event(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or row.get("event_id"),
        "ticker": row.get("ticker") or row.get("eventTicker"),
        "title": row.get("title") or row.get("name"),
        "start_time": row.get("start_time") or row.get("startTime") or row.get("event_time"),
        "sportsMarket": row.get("sportsMarket"),
        "contracts": [{
            "instrumentSymbol": c.get("instrumentSymbol") or c.get("ticker"),
            "label": c.get("label") or c.get("abbreviatedName"),
            "strike": c.get("strike"),
            "prices": c.get("prices"),
        } for c in (row.get("contracts") or [])[:3] if isinstance(c, Mapping)],
    }


def _kalshi_market(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "ticker", "title", "subtitle", "yes_sub_title", "no_sub_title", "open_time",
        "close_time", "expected_expiration_time", "rules_primary", "rules_secondary",
        "result", "status",
    )
    return {k: row.get(k) for k in keys if row.get(k) not in (None, "")}


def _kalshi_event(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "event_ticker", "series_ticker", "title", "sub_title", "strike_date",
        "strike_period", "mutually_exclusive", "category",
    )
    out = {k: row.get(k) for k in keys if row.get(k) not in (None, "")}
    out["markets"] = [_kalshi_market(m) for m in (row.get("markets") or [])[:3] if isinstance(m, Mapping)]
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as http:
        gbody = await _get(http, f"{GEMINI}/v1/prediction-markets/events", params={
            "status": "active", "sport": "american_football", "limit": 500, "offset": 0,
        })
        grows = [dict(x) for x in (gbody.get("data") or gbody.get("events") or []) if isinstance(x, Mapping)]
        nfl_g = [x for x in grows if str(x.get("ticker") or x.get("eventTicker") or "").upper().startswith("NFL-")]
        krows: dict[str, list[dict[str, Any]]] = {}
        for series in SERIES:
            body = await _get(http, f"{KALSHI}/events", params={
                "limit": 200, "status": "open", "series_ticker": series,
                "with_nested_markets": "true",
            })
            krows[series] = [dict(x) for x in (body.get("events") or []) if isinstance(x, Mapping)]
            await asyncio.sleep(0.30)
    out = {
        "status": "SPORTS_NFL_GK_SHAPE_DIAGNOSTIC",
        "gemini_nfl_events": len(nfl_g),
        "gemini_samples": [_gemini_event(x) for x in nfl_g[:6]],
        "kalshi_counts": {k: len(v) for k, v in krows.items()},
        "kalshi_samples": {k: [_kalshi_event(x) for x in v[:3]] for k, v in krows.items()},
        "read_only": True, "order_entry": False,
    }
    logger.warning("SPORTS_NFL_GK_SHAPE_DIAGNOSTIC %s", json.dumps(out, separators=(",", ":"), default=str))
    return out
