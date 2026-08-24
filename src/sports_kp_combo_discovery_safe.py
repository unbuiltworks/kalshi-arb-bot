"""Rate-limit-safe live discovery for the K/P same-day combo shadow.

Uses bounded pagination and preserves partial inventory on 429 instead of failing
the entire scan. Mapping remains fail-closed and reuses the structured parser.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from src.sports_kp_combo_discovery import classify_combo_row

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY = "https://gateway.polymarket.us"


async def _page(http: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
    response = await http.get(url, **kwargs)
    if response.status_code == 429:
        return {}, True
    response.raise_for_status()
    body = response.json()
    return (body if isinstance(body, dict) else {}), False


async def fetch_polymarket_bounded(http: httpx.AsyncClient, *, page_limit: int = 8, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    rate_limited = False
    for page_no in range(page_limit):
        body, limited = await _page(http, f"{POLY}/v1/markets", params={"limit": limit, "offset": page_no * limit})
        if limited:
            rate_limited = True
            break
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
    return rows, rate_limited


async def fetch_kalshi_bounded(http: httpx.AsyncClient, *, page_limit: int = 18, limit: int = 200) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    rate_limited = False
    for _ in range(page_limit):
        params: dict[str, Any] = {"limit": limit, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        body, limited = await _page(http, f"{KALSHI}/events", params=params)
        if limited:
            rate_limited = True
            break
        events = body.get("events") or []
        if not isinstance(events, list):
            break
        for event in events:
            if not isinstance(event, Mapping):
                continue
            for raw in event.get("markets") or []:
                if not isinstance(raw, Mapping):
                    continue
                parsed = classify_combo_row("kalshi", raw)
                if parsed:
                    rows.append(parsed)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return rows, rate_limited


async def discover_combo_candidates_safe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        (poly, poly_limited), (kalshi, kalshi_limited) = await asyncio.gather(
            fetch_polymarket_bounded(http), fetch_kalshi_bounded(http)
        )
    all_rows = poly + kalshi
    mapped = [row for row in all_rows if row.get("mapped")]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidates": len(all_rows),
        "mapped_structured": len(mapped),
        "unmapped_text_only": len(all_rows) - len(mapped),
        "by_venue": {
            "kalshi": sum(row.get("venue") == "kalshi" for row in all_rows),
            "polymarket": sum(row.get("venue") == "polymarket" for row in all_rows),
        },
        "rate_limited": {"kalshi": kalshi_limited, "polymarket": poly_limited},
        "mapped": mapped,
        "read_only": True,
        "order_entry": False,
    }
