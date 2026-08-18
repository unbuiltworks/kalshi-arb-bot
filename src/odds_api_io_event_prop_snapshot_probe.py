"""One-shot read-only Odds-API.io event prop snapshot probe."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Mapping

import httpx

API = "https://api.odds-api.io/v3/odds"


async def run(event_id: str) -> None:
    key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not key:
        raise SystemExit("ODDS_API_IO_API_KEY required")
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(API, params={
            "apiKey": key,
            "eventId": event_id,
            "bookmakers": "DraftKings,FanDuel",
        })
        response.raise_for_status()
        body: Any = response.json()
        print("ODDS_IO_PROP_SNAPSHOT_META " + json.dumps({
            "event_id": event_id,
            "remaining": response.headers.get("x-ratelimit-remaining", ""),
            "limit": response.headers.get("x-ratelimit-limit", ""),
            "reset": response.headers.get("x-ratelimit-reset", ""),
        }, separators=(",", ":")))
        if not isinstance(body, Mapping):
            print("ODDS_IO_PROP_SNAPSHOT_BODY " + json.dumps(body, separators=(",", ":"), default=str))
            return
        books = body.get("bookmakers") if isinstance(body.get("bookmakers"), Mapping) else {}
        out: dict[str, Any] = {}
        for book, markets in books.items():
            if not isinstance(markets, list):
                continue
            props = [
                m for m in markets
                if isinstance(m, Mapping)
                and str(m.get("name") or "").strip().lower() == "player props"
            ]
            if props:
                out[str(book)] = props
        print("ODDS_IO_PROP_SNAPSHOT " + json.dumps({
            "id": body.get("id"),
            "home": body.get("home"),
            "away": body.get("away"),
            "date": body.get("date"),
            "status": body.get("status"),
            "bookmakers": out,
        }, separators=(",", ":"), default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("event_id")
    args = parser.parse_args()
    asyncio.run(run(args.event_id))
