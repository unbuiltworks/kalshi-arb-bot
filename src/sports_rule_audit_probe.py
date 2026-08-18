"""Read-only targeted rule audit for one Gemini/Polymarket/Kalshi sports counterpart.

Fetches public venue metadata and prints only the exact matching event/market
objects needed for settlement-rule review. No authentication or order-entry code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any, Mapping

import httpx

from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_polymarket_mlb

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
logger = logging.getLogger("kalshi_arb.sports_rule_audit_probe")


def _contains(obj: Any, needle: str) -> bool:
    if isinstance(obj, Mapping):
        return any(_contains(value, needle) for value in obj.values())
    if isinstance(obj, list):
        return any(_contains(value, needle) for value in obj)
    return needle.lower() in str(obj).lower()


def _find_exact_node(obj: Any, needle: str) -> Any | None:
    """Return the smallest mapping that contains an exact string field match."""
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


async def run(gemini_symbol: str, poly_slug: str, kalshi_ticker: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, poly_events, k_response = await asyncio.gather(
            fetch_gemini_mlb(http),
            fetch_polymarket_mlb(http),
            http.get(f"{KALSHI}/markets/{kalshi_ticker}"),
        )
        k_response.raise_for_status()
        kalshi_body = k_response.json()

    g_parent = _find_parent(gemini_events, gemini_symbol)
    p_parent = _find_parent(poly_events, poly_slug)
    g_node = _find_exact_node(g_parent, gemini_symbol) if g_parent else None
    p_node = _find_exact_node(p_parent, poly_slug) if p_parent else None
    k_market = kalshi_body.get("market") if isinstance(kalshi_body, Mapping) else kalshi_body

    logger.info("SPORTS_RULE_AUDIT_GEMINI_NODE %s", _clip(g_node))
    logger.info("SPORTS_RULE_AUDIT_POLY_NODE %s", _clip(p_node))
    logger.info("SPORTS_RULE_AUDIT_KALSHI_MARKET %s", _clip(k_market))
    logger.info("SPORTS_RULE_AUDIT_PARENT_SUMMARY %s", json.dumps({
        "gemini_symbol": gemini_symbol,
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
    p.add_argument("--gemini-symbol", required=True)
    p.add_argument("--poly-slug", required=True)
    p.add_argument("--kalshi-ticker", required=True)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.gemini_symbol, args.poly_slug, args.kalshi_ticker))


if __name__ == "__main__":
    main()
