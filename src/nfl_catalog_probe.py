"""Read-only NFL catalog probe across Gemini, Kalshi and The Odds API.

Purpose: inspect the *current* market inventory/schema before building NFL
normalization. No authenticated prediction-market calls and no order-entry code.
Only The Odds API key is used for sportsbook discovery.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from typing import Any, Mapping

import httpx

GEMINI = "https://api.gemini.com/v1/prediction-markets/events"
KALSHI = "https://external-api.kalshi.com/trade-api/v2/events"
ODDS = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
KALSHI_SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")
ODDS_SPORTS = ("americanfootball_nfl_preseason", "americanfootball_nfl")


async def _get_json(http: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> tuple[Any, httpx.Response]:
    response = await http.get(url, params=params)
    response.raise_for_status()
    return response.json(), response


async def gemini_catalog(http: httpx.AsyncClient) -> dict[str, Any]:
    body, _ = await _get_json(
        http,
        GEMINI,
        params={"status": "active", "sport": "football", "limit": 500, "offset": 0},
    )
    events = body.get("data") or body.get("events") or [] if isinstance(body, Mapping) else []
    rows = [x for x in events if isinstance(x, Mapping)]
    nfl = []
    market_types: Counter[str] = Counter()
    contract_count = 0
    samples: list[dict[str, Any]] = []
    for event in rows:
        ticker = str(event.get("ticker") or event.get("eventTicker") or event.get("event_ticker") or "")
        sports = event.get("sportsMarket") if isinstance(event.get("sportsMarket"), Mapping) else {}
        league = str(sports.get("league") or sports.get("leagueCode") or "")
        if "NFL" not in ticker.upper() and league.upper() != "NFL":
            continue
        nfl.append(event)
        market_type = str(sports.get("marketType") or sports.get("type") or event.get("marketType") or "UNKNOWN")
        market_types[market_type] += 1
        contracts = [x for x in event.get("contracts") or [] if isinstance(x, Mapping)]
        contract_count += len(contracts)
        if len(samples) < 8:
            samples.append({
                "event_ticker": ticker,
                "title": str(event.get("title") or event.get("name") or "")[:120],
                "sports_market": dict(sports),
                "contracts": [
                    {
                        "instrumentSymbol": str(c.get("instrumentSymbol") or ""),
                        "eventOutcome": str(c.get("eventOutcome") or c.get("outcome") or ""),
                        "label": str(c.get("label") or c.get("name") or c.get("title") or "")[:80],
                    }
                    for c in contracts[:6]
                ],
            })
    return {
        "raw_football_events": len(rows),
        "nfl_events": len(nfl),
        "nfl_contracts": contract_count,
        "market_types": dict(market_types),
        "samples": samples,
    }


async def _kalshi_series(http: httpx.AsyncClient, series: str) -> list[dict[str, Any]]:
    last: httpx.Response | None = None
    for attempt in range(5):
        response = await http.get(
            KALSHI,
            params={"limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true"},
        )
        last = response
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json()
            return [dict(x) for x in body.get("events") or [] if isinstance(x, Mapping)] if isinstance(body, Mapping) else []
        await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
    assert last is not None
    last.raise_for_status()
    return []


async def kalshi_catalog(http: httpx.AsyncClient) -> dict[str, Any]:
    by_series: dict[str, int] = {}
    markets_by_series: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for series in KALSHI_SERIES:
        events = await _kalshi_series(http, series)
        by_series[series] = len(events)
        markets = 0
        for event in events:
            nested = [x for x in event.get("markets") or [] if isinstance(x, Mapping)]
            markets += len(nested)
            if len(samples) < 12:
                samples.append({
                    "series": series,
                    "event_ticker": str(event.get("event_ticker") or ""),
                    "title": str(event.get("title") or event.get("sub_title") or "")[:120],
                    "markets": [
                        {
                            "ticker": str(m.get("ticker") or ""),
                            "title": str(m.get("title") or m.get("subtitle") or "")[:90],
                            "yes_sub_title": str(m.get("yes_sub_title") or "")[:70],
                        }
                        for m in nested[:5]
                    ],
                })
        markets_by_series[series] = markets
        await asyncio.sleep(0.15)
    return {"events_by_series": by_series, "markets_by_series": markets_by_series, "samples": samples}


async def sportsbook_catalog(http: httpx.AsyncClient, api_key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sport in ODDS_SPORTS:
        response = await http.get(
            ODDS.format(sport=sport),
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )
        if response.status_code == 404:
            out[sport] = {"events": 0, "not_active": True}
            continue
        response.raise_for_status()
        body = response.json()
        rows = [x for x in body if isinstance(x, Mapping)] if isinstance(body, list) else []
        books = Counter()
        for event in rows:
            for book in event.get("bookmakers") or []:
                if isinstance(book, Mapping):
                    books[str(book.get("key") or book.get("title") or "unknown")] += 1
        out[sport] = {
            "events": len(rows),
            "books": dict(books),
            "quota": {
                "remaining": response.headers.get("x-requests-remaining", ""),
                "used": response.headers.get("x-requests-used", ""),
                "last_cost": response.headers.get("x-requests-last", ""),
            },
            "samples": [
                {
                    "id": str(e.get("id") or ""),
                    "commence_time": str(e.get("commence_time") or ""),
                    "away_team": str(e.get("away_team") or ""),
                    "home_team": str(e.get("home_team") or ""),
                }
                for e in rows[:6]
            ],
        }
    return out


async def run() -> dict[str, Any]:
    key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi, sportsbooks = await asyncio.gather(
            gemini_catalog(http), kalshi_catalog(http), sportsbook_catalog(http, key)
        )
    return {
        "status": "NFL_CATALOG_PROBE",
        "gemini": gemini,
        "kalshi": kalshi,
        "sportsbooks": sportsbooks,
        "read_only": True,
        "order_entry": False,
        "next_gate": "NORMALIZE_EXACT_IDENTITIES_THEN_AUDIT_RULES_BEFORE_ALERT_OR_EXECUTION",
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), separators=(",", ":"), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
