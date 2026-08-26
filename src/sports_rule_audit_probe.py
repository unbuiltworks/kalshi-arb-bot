"""Read-only targeted rule audit for exact prediction-market sports counterparts.

Fetches public venue metadata and prints the matching event/market objects needed
for settlement-rule review. Gemini is optional so a genuine Kalshi/Polymarket
pair can be audited without inventing a third leg. Polymarket discovery is sport-
generic so NFL/WNBA/etc. can use the same fail-closed audit path as MLB.
No auth or order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Mapping

import httpx

from src.universal_three_venue_sports import fetch_gemini_mlb

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY = "https://gateway.polymarket.us"
logger = logging.getLogger("kalshi_arb.sports_rule_audit_probe")


def _contains(obj: Any, needle: str) -> bool:
    if isinstance(obj, Mapping):
        return any(_contains(value, needle) for value in obj.values())
    if isinstance(obj, list):
        return any(_contains(value, needle) for value in obj)
    return needle.lower() in str(obj).lower()


def _find_exact_node(obj: Any, needle: str) -> Any | None:
    if isinstance(obj, Mapping):
        for value in obj.values():
            if isinstance(value, str) and value == needle:
                return dict(obj)
        for value in obj.values():
            found = _find_exact_node(value, needle)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_exact_node(value, needle)
            if found is not None:
                return found
    return None


def _find_parent(rows: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    for row in rows:
        if _contains(row, needle):
            return row
    return None


def _clip(obj: Any, limit: int = 60000) -> str:
    text = json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "...<TRUNCATED>"


async def _fetch_polymarket_sports(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Bounded current sports inventory for exact-slug metadata lookup."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=48)
    out: list[dict[str, Any]] = []
    for page in range(12):
        response = await http.get(
            f"{POLY}/v1/events",
            params={
                "limit": 100,
                "offset": page * 100,
                "active": "true",
                "closed": "false",
                "categories": "sports",
                "startTimeMax": horizon.isoformat().replace("+00:00", "Z"),
                "orderBy": "startTime",
                "orderDirection": "desc",
            },
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        rows = body.get("events") or [] if isinstance(body, Mapping) else []
        out.extend(dict(x) for x in rows if isinstance(x, Mapping))
        if len(rows) < 100:
            break
    return out


async def run(gemini_symbol: str, poly_slug: str, kalshi_ticker: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly_task = _fetch_polymarket_sports(http)
        kalshi_task = http.get(f"{KALSHI}/markets/{kalshi_ticker}")
        if gemini_symbol:
            gemini_events, poly_events, k_response = await asyncio.gather(
                fetch_gemini_mlb(http), poly_task, kalshi_task,
            )
        else:
            poly_events, k_response = await asyncio.gather(poly_task, kalshi_task)
            gemini_events = []
        k_response.raise_for_status()
        kalshi_body = k_response.json()

    g_parent = _find_parent(gemini_events, gemini_symbol) if gemini_symbol else None
    p_parent = _find_parent(poly_events, poly_slug)
    g_node = _find_exact_node(g_parent, gemini_symbol) if g_parent and gemini_symbol else None
    p_node = _find_exact_node(p_parent, poly_slug) if p_parent else None
    k_market = kalshi_body.get("market") if isinstance(kalshi_body, Mapping) else kalshi_body

    if gemini_symbol:
        logger.info("SPORTS_RULE_AUDIT_GEMINI_NODE %s", _clip(g_node))
    logger.info("SPORTS_RULE_AUDIT_POLY_NODE %s", _clip(p_node))
    logger.info("SPORTS_RULE_AUDIT_KALSHI_MARKET %s", _clip(k_market))
    logger.info("SPORTS_RULE_AUDIT_PARENT_SUMMARY %s", json.dumps({
        "gemini_symbol": gemini_symbol or None,
        "gemini_event_found": g_parent is not None,
        "gemini_node_found": g_node is not None,
        "gemini_parent": g_parent,
        "polymarket_slug": poly_slug,
        "polymarket_event_found": p_parent is not None,
        "polymarket_node_found": p_node is not None,
        "polymarket_parent": p_parent,
        "kalshi_ticker": kalshi_ticker,
        "kalshi_market_found": bool(k_market),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), sort_keys=True, default=str)[:120000])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gemini-symbol", default="")
    p.add_argument("--poly-slug", required=True)
    p.add_argument("--kalshi-ticker", required=True)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.gemini_symbol, args.poly_slug, args.kalshi_ticker))


if __name__ == "__main__":
    main()
