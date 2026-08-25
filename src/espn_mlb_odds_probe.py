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


def _shape(value: Any, depth: int = 0) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {"_keys": sorted(str(k) for k in value.keys())}
        if depth < 2:
            for key, nested in value.items():
                if isinstance(nested, (str, int, float, bool, type(None))):
                    out[str(key)] = nested
                elif isinstance(nested, Mapping):
                    out[str(key)] = _shape(nested, depth + 1)
        return out
    if isinstance(value, list):
        return {"_type": "list", "count": len(value)}
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(type(value).__name__)


def _safe_odds_item(item: Mapping[str, Any]) -> dict[str, Any]:
    provider = item.get("provider") if isinstance(item.get("provider"), Mapping) else {}
    home = item.get("homeTeamOdds") if isinstance(item.get("homeTeamOdds"), Mapping) else {}
    away = item.get("awayTeamOdds") if isinstance(item.get("awayTeamOdds"), Mapping) else {}
    return {
        "provider_id": provider.get("id"),
        "provider_name": provider.get("name"),
        "details": item.get("details"),
        "home_moneyline": home.get("moneyLine"),
        "away_moneyline": away.get("moneyLine"),
        "home_open_shape": _shape(home.get("open")),
        "home_current_shape": _shape(home.get("current")),
        "home_close_shape": _shape(home.get("close")),
        "away_open_shape": _shape(away.get("open")),
        "away_current_shape": _shape(away.get("current")),
        "away_close_shape": _shape(away.get("close")),
        "market_open_shape": _shape(item.get("open")),
        "market_current_shape": _shape(item.get("current")),
        "market_close_shape": _shape(item.get("close")),
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
            odds_json: Any = odds_response.json() if odds_response.status_code == 200 else None
            if isinstance(odds_json, Mapping):
                items = odds_json.get("items") or odds_json.get("odds") or []
            elif isinstance(odds_json, list):
                items = odds_json
            else:
                items = []
            rows.append({
                "event_id": event_id,
                "competition_id": comp_id,
                "name": event.get("name"),
                "start": event.get("date"),
                "odds_status": odds_response.status_code,
                "odds_items": [_safe_odds_item(x) for x in items[:3] if isinstance(x, Mapping)],
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
