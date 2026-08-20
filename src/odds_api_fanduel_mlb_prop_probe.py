"""One-shot read-only FanDuel MLB player-prop coverage probe via The Odds API.

Chooses the nearest future MLB event, asks only for FanDuel, and requests the prop
families relevant to our prediction-market counterpart engine.  It never places a
wager and never prints the API key.  Output includes actual x-requests-* headers so
we can decide scan cadence from measured quota cost rather than guessing.
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
BOOKMAKER = "fanduel"
MARKETS = (
    "batter_home_runs",
    "batter_home_runs_alternate",
    "batter_hits",
    "batter_hits_alternate",
    "batter_total_bases",
    "batter_total_bases_alternate",
    "batter_hits_runs_rbis",
    "batter_hits_runs_rbis_alternate",
    "pitcher_strikeouts",
    "pitcher_strikeouts_alternate",
)


def _parse_time(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _quota(response: httpx.Response) -> dict[str, str]:
    return {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
        "last_cost": response.headers.get("x-requests-last", ""),
    }


def summarize_event(body: Mapping[str, Any], response: httpx.Response) -> dict[str, Any]:
    bookmaker = next(
        (row for row in body.get("bookmakers") or [] if isinstance(row, Mapping) and str(row.get("key") or "").lower() == BOOKMAKER),
        None,
    )
    markets = list(bookmaker.get("markets") or []) if isinstance(bookmaker, Mapping) else []
    rows: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        key = str(market.get("key") or "")
        outcomes = [x for x in market.get("outcomes") or [] if isinstance(x, Mapping)]
        players = sorted({str(x.get("description") or "").strip() for x in outcomes if str(x.get("description") or "").strip()})
        lines = sorted({str(x.get("point")) for x in outcomes if x.get("point") is not None})
        sample = [
            {
                "name": x.get("name"),
                "player": x.get("description"),
                "point": x.get("point"),
                "price": x.get("price"),
            }
            for x in outcomes[:6]
        ]
        rows.append({
            "market": key,
            "last_update": market.get("last_update"),
            "outcomes": len(outcomes),
            "players": len(players),
            "lines": len(lines),
            "sample": sample,
        })
    rows.sort(key=lambda x: x["market"])
    return {
        "status": "FANDUEL_MLB_PROP_COVERAGE_PROBE",
        "event_id": body.get("id"),
        "commence_time": body.get("commence_time"),
        "away_team": body.get("away_team"),
        "home_team": body.get("home_team"),
        "requested_markets": list(MARKETS),
        "returned_markets": [x["market"] for x in rows],
        "returned_market_count": len(rows),
        "market_summaries": rows,
        "quota": _quota(response),
        "read_only": True,
        "order_entry": False,
    }


async def run() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=30.0) as http:
        events_response = await http.get(EVENTS_URL, params={"apiKey": api_key, "dateFormat": "iso"})
        events_response.raise_for_status()
        events = [x for x in events_response.json() if isinstance(x, Mapping)]
        future = []
        for event in events:
            start = _parse_time(event.get("commence_time"))
            if start is not None and start > now:
                future.append((start, event))
        if not future:
            return {
                "status": "FANDUEL_MLB_PROP_COVERAGE_PROBE",
                "future_events": 0,
                "events_quota": _quota(events_response),
                "read_only": True,
                "order_entry": False,
            }
        future.sort(key=lambda x: x[0])
        _start, event = future[0]
        event_id = str(event.get("id") or "")
        response = await http.get(
            EVENT_ODDS_URL.format(event_id=event_id),
            params={
                "apiKey": api_key,
                "bookmakers": BOOKMAKER,
                "markets": ",".join(MARKETS),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )
        response.raise_for_status()
        body = response.json()
        result = summarize_event(body if isinstance(body, Mapping) else {}, response)
        result["future_events_seen"] = len(future)
        result["events_endpoint_quota"] = _quota(events_response)
        return result


def main() -> None:
    print(json.dumps(asyncio.run(run()), separators=(",", ":"), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
