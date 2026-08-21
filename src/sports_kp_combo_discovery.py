"""Read-only discovery helpers for Kalshi/Polymarket 2-3 leg sports combos.

The parser is deliberately conservative. A market is mapped to component events
only when the payload exposes explicit structured legs/components/selections.
Text-only combo-looking rows are surfaced as unmapped research candidates and
can never feed the executable optimizer.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY = "https://gateway.polymarket.us"

_COMBO_WORDS = ("combo", "parlay", "multi-leg", "multileg", "double", "triple")


def _text(row: Mapping[str, Any]) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "question", "subtitle", "name", "ticker", "slug")).lower()


def looks_like_combo(row: Mapping[str, Any]) -> bool:
    if any(k in row for k in ("legs", "components", "selections")):
        return True
    return any(word in _text(row) for word in _COMBO_WORDS)


def explicit_legs(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return normalized explicit legs only; never infer a leg from prose."""
    raw = None
    for key in ("legs", "components", "selections"):
        value = row.get(key)
        if isinstance(value, list):
            raw = value
            break
    if raw is None:
        return []
    out: list[dict[str, Any]] = []
    for leg in raw:
        if not isinstance(leg, Mapping):
            return []
        event_id = (
            leg.get("eventId") or leg.get("event_id") or leg.get("eventTicker")
            or leg.get("event_ticker") or leg.get("gameId") or leg.get("game_id")
        )
        market_id = (
            leg.get("marketId") or leg.get("market_id") or leg.get("ticker")
            or leg.get("marketTicker") or leg.get("slug")
        )
        outcome = (
            leg.get("outcome") or leg.get("side") or leg.get("selection")
            or leg.get("result")
        )
        if not event_id or not market_id or outcome in (None, ""):
            return []
        out.append({
            "event_id": str(event_id),
            "market_id": str(market_id),
            "outcome": str(outcome),
        })
    return out if len(out) in {2, 3} else []


def classify_combo_row(venue: str, row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not looks_like_combo(row):
        return None
    legs = explicit_legs(row)
    market_id = str(row.get("ticker") or row.get("slug") or row.get("id") or "")
    return {
        "venue": venue,
        "market_id": market_id,
        "title": str(row.get("title") or row.get("question") or row.get("name") or ""),
        "legs": legs,
        "leg_count": len(legs),
        "mapped": len(legs) in {2, 3},
        "status": "STRUCTURED_COMPONENTS_DISCOVERED" if legs else "TEXT_ONLY_COMBO_CANDIDATE_UNMAPPED",
        "raw": dict(row),
    }


async def _get_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def fetch_polymarket_combo_candidates(http: httpx.AsyncClient, *, limit: int = 500) -> list[dict[str, Any]]:
    """Scan current US market inventory and surface structured combo rows."""
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(20):
        body = await _get_json(http, f"{POLY}/v1/markets", params={"limit": limit, "offset": offset})
        page = body.get("markets") or body.get("data") or []
        if not isinstance(page, list):
            break
        for raw in page:
            if isinstance(raw, Mapping):
                parsed = classify_combo_row("polymarket", raw)
                if parsed:
                    rows.append(parsed)
        if len(page) < limit:
            break
        offset += limit
    return rows


async def fetch_kalshi_combo_candidates(http: httpx.AsyncClient, *, limit: int = 200) -> list[dict[str, Any]]:
    """Scan open Kalshi events with nested markets for structured combo rows."""
    rows: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(50):
        params: dict[str, Any] = {"limit": limit, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        body = await _get_json(http, f"{KALSHI}/events", params=params)
        events = body.get("events") or []
        if not isinstance(events, list):
            break
        for event in events:
            if not isinstance(event, Mapping):
                continue
            for raw in event.get("markets") or []:
                if not isinstance(raw, Mapping):
                    continue
                enriched = dict(raw)
                if "eventTicker" not in enriched and event.get("event_ticker"):
                    enriched["eventTicker"] = event.get("event_ticker")
                parsed = classify_combo_row("kalshi", enriched)
                if parsed:
                    rows.append(parsed)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return rows


async def discover_combo_candidates() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly, kalshi = await asyncio.gather(
            fetch_polymarket_combo_candidates(http),
            fetch_kalshi_combo_candidates(http),
        )
    all_rows = poly + kalshi
    mapped = [x for x in all_rows if x["mapped"]]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidates": len(all_rows),
        "mapped_structured": len(mapped),
        "unmapped_text_only": len(all_rows) - len(mapped),
        "by_venue": {
            "kalshi": sum(x["venue"] == "kalshi" for x in all_rows),
            "polymarket": sum(x["venue"] == "polymarket" for x in all_rows),
        },
        "mapped": mapped,
        "unmapped": [x for x in all_rows if not x["mapped"]],
        "read_only": True,
        "order_entry": False,
    }
