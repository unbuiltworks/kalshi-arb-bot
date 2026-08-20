"""One-shot The Odds API MLB player-prop all-US-books coverage/cost probe.

Chooses the nearest future MLB event, requests the six player-prop markets already
validated in the FanDuel probe using regions=us, and reports bookmaker/market coverage
plus response credit cost. No prediction pricing, alerts, or order entry.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping

import httpx

EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
MARKETS = (
    "batter_hits_alternate",
    "batter_hits_runs_rbis_alternate",
    "batter_home_runs_alternate",
    "batter_total_bases_alternate",
    "pitcher_strikeouts",
    "pitcher_strikeouts_alternate",
)


def _dt(value: Any):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=30.0) as http:
        events_response = await http.get(EVENTS_URL, params={"apiKey":api_key,"dateFormat":"iso"})
        events_response.raise_for_status()
        payload = events_response.json()
        future = [dict(row) for row in payload if isinstance(row, Mapping) and (_dt(row.get("commence_time")) or now) > now] if isinstance(payload, list) else []
        future.sort(key=lambda row: _dt(row.get("commence_time")) or datetime.max.replace(tzinfo=timezone.utc))
        if not future:
            return {"status":"ODDS_API_MLB_PROP_ALLBOOKS_NO_FUTURE_EVENT","read_only":True,"order_entry":False}
        event = future[0]; event_id = str(event.get("id") or "")
        response = await http.get(
            EVENT_ODDS_URL.format(event_id=event_id),
            params={"apiKey":api_key,"regions":"us","markets":",".join(MARKETS),"oddsFormat":"decimal","dateFormat":"iso"},
        )
        response.raise_for_status()
        body = response.json()

    books = []
    market_presence = Counter()
    outcomes = Counter()
    for bookmaker in body.get("bookmakers") or [] if isinstance(body, Mapping) else []:
        if not isinstance(bookmaker, Mapping):
            continue
        key = str(bookmaker.get("key") or bookmaker.get("title") or "")
        markets = [m for m in bookmaker.get("markets") or [] if isinstance(m, Mapping)]
        market_keys = sorted(str(m.get("key") or "") for m in markets if m.get("key"))
        books.append({
            "book": key,
            "market_count": len(market_keys),
            "markets": market_keys,
            "outcomes": sum(len(m.get("outcomes") or []) for m in markets),
        })
        for market in markets:
            mk = str(market.get("key") or "")
            market_presence[mk] += 1
            outcomes[mk] += len(market.get("outcomes") or [])
    return {
        "status":"ODDS_API_MLB_PROP_ALLBOOKS_PROBE",
        "event_id":event_id,
        "away_team":event.get("away_team"),
        "home_team":event.get("home_team"),
        "commence_time":event.get("commence_time"),
        "requested_markets":list(MARKETS),
        "bookmaker_count":len(books),
        "books":books,
        "bookmakers_per_market":dict(market_presence),
        "outcomes_per_market":dict(outcomes),
        "quota":{
            "remaining":response.headers.get("x-requests-remaining",""),
            "used":response.headers.get("x-requests-used",""),
            "last_cost":response.headers.get("x-requests-last",""),
        },
        "read_only":True,
        "order_entry":False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
