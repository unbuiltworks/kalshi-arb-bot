"""One-shot read-only probe of live Kalshi combo candidate payload shapes."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from src.sports_kp_combo_discovery_safe import fetch_kalshi_bounded

logger = logging.getLogger("kalshi_arb.sports_kp_combo_shape_probe")


async def run() -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        rows, limited = await fetch_kalshi_bounded(http, page_limit=18)
    sample = []
    for row in rows[:20]:
        raw = row.get("raw") or {}
        sample.append({
            "market_id": row.get("market_id"),
            "title": row.get("title"),
            "status": row.get("status"),
            "raw_keys": sorted(raw.keys()),
            "event_ticker": raw.get("event_ticker") or raw.get("eventTicker"),
            "series_ticker": raw.get("series_ticker") or raw.get("seriesTicker"),
            "subtitle": raw.get("subtitle"),
            "yes_sub_title": raw.get("yes_sub_title"),
            "no_sub_title": raw.get("no_sub_title"),
            "rules_primary": raw.get("rules_primary"),
            "rules_secondary": raw.get("rules_secondary"),
            "custom_strike": raw.get("custom_strike"),
        })
    print("SPORTS_KP_COMBO_SHAPE " + json.dumps({
        "candidate_count": len(rows),
        "rate_limited": limited,
        "sample": sample,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
