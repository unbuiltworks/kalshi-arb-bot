"""One-shot read-only probe for Kalshi sports Multivariate Event (combo) markets.

Uses the dedicated MVE endpoints so every combo market carries authoritative
mve_selected_legs metadata. No order creation or order entry occurs here.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
SPORT_WORDS = (
    "mlb", "nfl", "football", "baseball", "nba", "basketball", "nhl", "hockey",
    "soccer", "tennis", "golf", "ufc", "mma", "ncaaf", "ncaab", "wnba",
)
SPORT_TICKER_HINTS = (
    "MLB", "NFL", "NCAAF", "CFB", "NBA", "NCAAB", "CBB", "NHL", "WNBA",
    "SOCCER", "TENNIS", "GOLF", "UFC", "MMA", "GAME", "FOOTBALLSTATS",
)


def _sportsish(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(k) or "") for k in ("title", "description", "series_ticker", "functional_description")).lower()
    tickers = " ".join(str(x) for x in (row.get("associated_event_tickers") or []))
    return any(w in text for w in SPORT_WORDS) or any(h in tickers.upper() for h in SPORT_TICKER_HINTS)


async def _get(http: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    r = await http.get(f"{BASE}{path}", params=params)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, dict) else {}


async def main_async() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        collections: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(10):
            params: dict[str, Any] = {"status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            body = await _get(http, "/multivariate_event_collections", **params)
            page = [dict(x) for x in body.get("multivariate_contracts") or [] if isinstance(x, dict)]
            collections.extend(page)
            cursor = str(body.get("cursor") or "")
            if not cursor:
                break

        sports = [x for x in collections if _sportsish(x)]
        combo_markets: list[dict[str, Any]] = []
        for coll in sports[:50]:
            ticker = str(coll.get("collection_ticker") or "")
            if not ticker:
                continue
            cursor = ""
            for _ in range(5):
                params = {"collection_ticker": ticker, "with_nested_markets": "true", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                body = await _get(http, "/events/multivariate", **params)
                for event in body.get("events") or []:
                    if not isinstance(event, dict):
                        continue
                    for market in event.get("markets") or []:
                        if not isinstance(market, dict):
                            continue
                        legs = market.get("mve_selected_legs") or []
                        if not isinstance(legs, list) or len(legs) not in {2, 3}:
                            continue
                        combo_markets.append({
                            "collection_ticker": ticker,
                            "collection_title": coll.get("title"),
                            "series_ticker": coll.get("series_ticker"),
                            "event_ticker": event.get("event_ticker"),
                            "event_title": event.get("title"),
                            "market_ticker": market.get("ticker"),
                            "market_title": market.get("title") or market.get("yes_sub_title"),
                            "close_time": market.get("close_time"),
                            "yes_ask_dollars": market.get("yes_ask_dollars"),
                            "yes_ask_size_fp": market.get("yes_ask_size_fp"),
                            "no_ask_dollars": market.get("no_ask_dollars"),
                            "no_ask_size_fp": market.get("no_ask_size_fp"),
                            "legs": legs,
                            "is_provisional": market.get("is_provisional"),
                        })
                cursor = str(body.get("cursor") or "")
                if not cursor:
                    break

    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "open_collections": len(collections),
        "sports_collections": len(sports),
        "sports_collection_sample": [
            {
                "collection_ticker": x.get("collection_ticker"),
                "series_ticker": x.get("series_ticker"),
                "title": x.get("title"),
                "close_date": x.get("close_date"),
                "associated_event_tickers": x.get("associated_event_tickers"),
                "is_all_yes": x.get("is_all_yes"),
                "size_min": x.get("size_min"),
                "size_max": x.get("size_max"),
            }
            for x in sports[:50]
        ],
        "two_leg_markets": sum(len(x["legs"]) == 2 for x in combo_markets),
        "three_leg_markets": sum(len(x["legs"]) == 3 for x in combo_markets),
        "combo_markets": combo_markets[:200],
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    print("SPORTS_KP_MVE_PROBE " + json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
