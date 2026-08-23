"""Read-only direct inspection of recently manufactured Kalshi sports MVEs.

This module is intentionally isolated from every order-entry path.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_TICKER = "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-054D7EDE6EB"


async def run_once() -> dict[str, Any]:
    ticker = os.getenv("MVE_SHAPE_TICKER", DEFAULT_TICKER).strip()
    async with httpx.AsyncClient(timeout=20.0) as http:
        r = await http.get(f"{BASE}/markets/{ticker}")
        direct_body = r.json() if r.content else {}
        target = direct_body.get("market") if isinstance(direct_body, dict) and isinstance(direct_body.get("market"), dict) else direct_body
        target = target if isinstance(target, dict) else {}

        cursor = ""
        events_seen = markets_seen = markets_with_legs = 0
        target_in_listing = False
        target_listing: dict[str, Any] = {}
        listing_samples: list[dict[str, Any]] = []
        page_statuses: list[int] = []
        for _ in range(8):
            params: dict[str, Any] = {"limit": 200, "with_nested_markets": "true", "status": "open"}
            if cursor:
                params["cursor"] = cursor
            er = await http.get(f"{BASE}/events/multivariate", params=params)
            page_statuses.append(er.status_code)
            if er.status_code != 200:
                break
            body = er.json() if er.content else {}
            for event in body.get("events") or []:
                if not isinstance(event, dict):
                    continue
                events_seen += 1
                for m in event.get("markets") or []:
                    if not isinstance(m, dict):
                        continue
                    markets_seen += 1
                    legs = m.get("mve_selected_legs") or []
                    if legs:
                        markets_with_legs += 1
                        if len(listing_samples) < 5:
                            listing_samples.append({
                                "ticker": m.get("ticker"),
                                "status": m.get("status"),
                                "expected_expiration_time": m.get("expected_expiration_time"),
                                "close_time": m.get("close_time"),
                                "leg_count": len(legs),
                            })
                    if str(m.get("ticker") or "") == ticker:
                        target_in_listing = True
                        target_listing = {
                            "ticker": m.get("ticker"),
                            "status": m.get("status"),
                            "expected_expiration_time": m.get("expected_expiration_time"),
                            "expiration_time": m.get("expiration_time"),
                            "close_time": m.get("close_time"),
                            "mve_selected_legs": m.get("mve_selected_legs"),
                            "keys": sorted(m.keys()),
                        }
            cursor = str(body.get("cursor") or "") if isinstance(body, dict) else ""
            if not cursor:
                break

    out = {
        "status": "SPORTS_MVE_DIRECT_SHAPE_V3_OPEN_ONLY",
        "http_status": r.status_code,
        "ticker": ticker,
        "direct": {
            "market_status": target.get("status"),
            "event_ticker": target.get("event_ticker"),
            "expected_expiration_time": target.get("expected_expiration_time"),
            "expiration_time": target.get("expiration_time"),
            "close_time": target.get("close_time"),
            "mve_selected_legs": target.get("mve_selected_legs"),
            "keys": sorted(target.keys()),
        },
        "multivariate_listing": {
            "filter": "status=open",
            "page_statuses": page_statuses,
            "events_seen": events_seen,
            "markets_seen": markets_seen,
            "markets_with_legs": markets_with_legs,
            "target_in_listing": target_in_listing,
            "target_listing": target_listing or None,
            "samples_with_legs": listing_samples,
        },
        "read_only": True,
        "market_creation": False,
        "rfq": False,
        "order_entry": False,
    }
    print("SPORTS_MVE_DIRECT_SHAPE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
