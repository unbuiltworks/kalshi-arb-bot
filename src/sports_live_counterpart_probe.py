"""One-shot read-only counterpart probe for currently live Polymarket sports.

Discovers explicitly live Polymarket league events, then searches current Kalshi
open events and Gemini active sports events for participant-name counterparts.
This is discovery only: no MVE creation, RFQ acceptance, or order entry.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Mapping

import httpx

from src.auth import KalshiClient
from src.sports_live_poly_inventory import LEAGUES, _league, _event_row
from datetime import datetime, timezone

GEMINI = "https://api.gemini.com"


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    stop = {"versus", "with", "from", "will", "match", "game", "event"}
    return {w for w in words if w not in stop}


def _safe_kalshi(event: Mapping[str, Any]) -> dict[str, Any]:
    markets = []
    for m in event.get("markets") or []:
        if not isinstance(m, Mapping):
            continue
        markets.append({k: m.get(k) for k in (
            "ticker", "title", "subtitle", "yes_sub_title", "no_sub_title", "status",
            "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars",
            "close_time", "expected_expiration_time",
        ) if m.get(k) is not None})
    return {k: event.get(k) for k in ("event_ticker", "series_ticker", "title", "subtitle", "category") if event.get(k) is not None} | {"markets": markets}


async def _all_kalshi_open(client: KalshiClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(30):
        params: dict[str, Any] = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/events", params=params)
        r.raise_for_status()
        body = r.json()
        out.extend(dict(x) for x in (body.get("events") or []) if isinstance(x, Mapping))
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return out


async def _gemini_active(http: httpx.AsyncClient, sport: str) -> list[dict[str, Any]]:
    try:
        r = await http.get(f"{GEMINI}/v1/prediction-markets/events", params={"status":"active", "sport":sport, "limit":500, "offset":0})
        r.raise_for_status()
        body = r.json()
        return [dict(x) for x in (body.get("data") or body.get("events") or []) if isinstance(x, Mapping)]
    except Exception:
        return []


def _match_rows(target: Mapping[str, Any], rows: list[dict[str, Any]], *, min_overlap: int = 2) -> list[dict[str, Any]]:
    target_text = " ".join(str(target.get(k) or "") for k in ("title", "name", "description", "slug"))
    tt = _tokens(target_text)
    scored = []
    for row in rows:
        text = json.dumps(row, default=str)
        overlap = tt & _tokens(text)
        if len(overlap) >= min_overlap:
            scored.append((len(overlap), row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:10]]


async def main_async() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=15.0) as http:
        poly_results = await asyncio.gather(*(_league(http, league) for league in LEAGUES))
        gem_tennis_task = asyncio.create_task(_gemini_active(http, "tennis"))
        live: list[dict[str, Any]] = []
        for league, rows, _ in poly_results:
            for raw in rows:
                event = _event_row(league, raw, now)
                if event is not None:
                    live.append(event)
        gem_tennis = await gem_tennis_task

    kalshi = KalshiClient()
    try:
        kalshi_rows = await _all_kalshi_open(kalshi)
    finally:
        await kalshi.close()

    results = []
    for event in live:
        kmatches = _match_rows(event, kalshi_rows)
        gmatches = _match_rows(event, gem_tennis)
        results.append({
            "polymarket": {k: event.get(k) for k in ("id","slug","title","league","period","score","startTime","endDate","hours_to_end")},
            "kalshi_matches": [_safe_kalshi(x) for x in kmatches],
            "gemini_matches": [{k: x.get(k) for k in ("id","eventId","title","name","slug","sport","status","startTime","endTime") if x.get(k) is not None} for x in gmatches],
            "has_kalshi_candidate": bool(kmatches),
            "has_gemini_candidate": bool(gmatches),
        })
    return {
        "status":"LIVE_SPORTS_COUNTERPART_PROBE",
        "live_polymarket_events":len(live),
        "kalshi_open_events_scanned":len(kalshi_rows),
        "gemini_tennis_events_scanned":len(gem_tennis),
        "results":results,
        "read_only":True,
        "order_entry":False,
    }


def main() -> None:
    print("LIVE_SPORTS_COUNTERPART_PROBE " + json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
