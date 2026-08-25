"""Read-only probe for ESPN's archived MLB odds surface.

No credentials, no order entry, no production imports. Writes only a structural
summary so the research branch can verify whether historical game-line prices are
available before adapting the EV backtest.
"""
from __future__ import annotations

import asyncio
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import httpx

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"


def _safe_odds_item(item: Mapping[str, Any]) -> dict[str, Any]:
    provider = item.get("provider") if isinstance(item.get("provider"), Mapping) else {}
    home = item.get("homeTeamOdds") if isinstance(item.get("homeTeamOdds"), Mapping) else {}
    away = item.get("awayTeamOdds") if isinstance(item.get("awayTeamOdds"), Mapping) else {}
    return {
        "item_keys": sorted(str(k) for k in item.keys()),
        "provider_id": provider.get("id"),
        "provider_name": provider.get("name"),
        "details": item.get("details"),
        "over_under": item.get("overUnder"),
        "home_moneyline": home.get("moneyLine"),
        "away_moneyline": away.get("moneyLine"),
        "home_favorite": home.get("favorite"),
        "away_favorite": away.get("favorite"),
        "last_update": item.get("lastUpdate") or item.get("lastUpdated") or item.get("last_update"),
        "last_modified": item.get("lastModified") or item.get("last_modified"),
        "timestamp": item.get("timestamp"),
        "date": item.get("date"),
        "created": item.get("created") or item.get("createdAt"),
        "updated": item.get("updated") or item.get("updatedAt"),
        "home_keys": sorted(str(k) for k in home.keys()),
        "away_keys": sorted(str(k) for k in away.keys()),
    }


async def run(day: date) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
        sb = await http.get(SCOREBOARD, params={"dates": day.strftime("%Y%m%d"), "limit": 100})
        sb.raise_for_status()
        payload = sb.json()
        events = payload.get("events") if isinstance(payload, Mapping) else []
        rows = []
        for event in (events or [])[:3]:
            if not isinstance(event, Mapping):
                continue
            competitions = event.get("competitions") or []
            comp = competitions[0] if competitions and isinstance(competitions[0], Mapping) else {}
            event_id = str(event.get("id") or "")
            comp_id = str(comp.get("id") or event_id)
            if not event_id or not comp_id:
                continue
            odds_url = f"{CORE}/events/{event_id}/competitions/{comp_id}/odds"
            odds_response = await http.get(odds_url, params={"limit": 100})
            odds_json: Any = None
            if odds_response.status_code == 200:
                try:
                    odds_json = odds_response.json()
                except Exception:
                    odds_json = None
            if isinstance(odds_json, Mapping):
                items = odds_json.get("items") or odds_json.get("odds") or []
                top_keys = sorted(str(k) for k in odds_json.keys())[:30]
            elif isinstance(odds_json, list):
                items = odds_json
                top_keys = []
            else:
                items = []
                top_keys = []
            rows.append({
                "event_id": event_id,
                "competition_id": comp_id,
                "name": event.get("name"),
                "start": event.get("date"),
                "odds_status": odds_response.status_code,
                "odds_top_keys": top_keys,
                "odds_items": [_safe_odds_item(x) for x in items[:10] if isinstance(x, Mapping)],
            })
        return {
            "status": "ESPN_MLB_HISTORICAL_ODDS_PROBE_READ_ONLY",
            "date": day.isoformat(),
            "scoreboard_status": sb.status_code,
            "event_count": len(events or []),
            "sample_events": rows,
        }


if __name__ == "__main__":
    result = asyncio.run(run(date(2026, 7, 9)))
    out = Path("research/results/espn_mlb_odds_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))
