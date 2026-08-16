"""Read-only inventory of active sports market families on Polymarket US and Kalshi.

This is intentionally an inventory tool, not a matcher. It measures the live venue
taxonomies so prop support can be added by explicit adapter mappings rather than by
fuzzy free-text guesses. No authentication or order-entry code is used.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import logging
from typing import Any

import httpx

POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
logger = logging.getLogger("kalshi_arb.sports_universe_inventory")

POLY_PAGE_SIZE = 500
POLY_MAX_RECORDS = 30000
KALSHI_PAGE_SIZE = 200
KALSHI_MAX_PAGES = 100


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_sports_category(value: Any) -> bool:
    return _lower(value) in {"sports", "sport"}


async def fetch_poly_sports(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for offset in range(0, POLY_MAX_RECORDS, POLY_PAGE_SIZE):
        response = await http.get(
            f"{POLY}/v1/markets",
            params={
                "limit": POLY_PAGE_SIZE,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
            },
        )
        response.raise_for_status()
        rows = list(response.json().get("markets", []))
        out.extend(row for row in rows if _is_sports_category(row.get("category")))
        if len(rows) < POLY_PAGE_SIZE:
            break
    return out


async def fetch_kalshi_sports(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(KALSHI_MAX_PAGES):
        params: dict[str, Any] = {
            "limit": KALSHI_PAGE_SIZE,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        response = await http.get(f"{KALSHI}/events", params=params)
        if response.status_code == 429:
            await asyncio.sleep(2.0)
            continue
        response.raise_for_status()
        body = response.json()
        for event in body.get("events", []):
            if _is_sports_category(event.get("category")):
                out.append(event)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
        await asyncio.sleep(0.05)
    return out


def summarize_poly(rows: list[dict[str, Any]]) -> dict[str, Any]:
    market_types: Counter[str] = Counter()
    sports_types: Counter[str] = Counter()
    leagues: Counter[str] = Counter()
    samples: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        market_type = str(row.get("marketType") or "UNKNOWN")
        sports_type = str(row.get("sportsMarketType") or "UNKNOWN")
        market_types[market_type] += 1
        sports_types[sports_type] += 1
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        league = str(
            row.get("league")
            or row.get("sportsLeague")
            or subject.get("league")
            or row.get("subcategory")
            or "UNKNOWN"
        )
        leagues[league] += 1
        key = sports_type
        if len(samples[key]) < 3:
            samples[key].append({
                "slug": str(row.get("slug") or ""),
                "question": str(row.get("question") or row.get("title") or ""),
                "line": str(row.get("line") or ""),
                "market_type": market_type,
            })

    return {
        "markets": len(rows),
        "market_types": dict(market_types.most_common()),
        "sports_market_types": dict(sports_types.most_common()),
        "leagues": dict(leagues.most_common()),
        "samples_by_sports_market_type": dict(sorted(samples.items())),
    }


def summarize_kalshi(events: list[dict[str, Any]]) -> dict[str, Any]:
    series: Counter[str] = Counter()
    strike_types: Counter[str] = Counter()
    event_series_samples: dict[str, list[dict[str, str]]] = defaultdict(list)
    total_markets = 0

    for event in events:
        series_ticker = str(event.get("series_ticker") or "UNKNOWN")
        markets = list(event.get("markets") or [])
        total_markets += len(markets)
        series[series_ticker] += len(markets)
        for market in markets:
            strike = str(market.get("strike_type") or "NONE")
            strike_types[strike] += 1
            if len(event_series_samples[series_ticker]) < 3:
                event_series_samples[series_ticker].append({
                    "ticker": str(market.get("ticker") or ""),
                    "event_title": str(event.get("title") or event.get("sub_title") or ""),
                    "market_title": str(market.get("title") or market.get("yes_sub_title") or ""),
                    "yes_sub_title": str(market.get("yes_sub_title") or ""),
                    "strike_type": strike,
                })

    return {
        "events": len(events),
        "markets": total_markets,
        "series_tickers": dict(series.most_common()),
        "strike_types": dict(strike_types.most_common()),
        "samples_by_series": dict(sorted(event_series_samples.items())),
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly, kalshi = await asyncio.gather(fetch_poly_sports(http), fetch_kalshi_sports(http))
    summary = {
        "polymarket_us": summarize_poly(poly),
        "kalshi": summarize_kalshi(kalshi),
        "read_only": True,
        "order_entry": False,
    }
    logger.info("SPORTS_UNIVERSE_INVENTORY %s", json.dumps(summary, separators=(",", ":")))
    print(json.dumps(summary, sort_keys=True))
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
