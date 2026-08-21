"""Bounded read-only inventory of currently live Polymarket US sports events.

This probe is intentionally order-free. It queries documented public league-event
endpoints, keeps events explicitly marked live and not ended/closed, and ranks
short-duration inventory first so the combo engine can focus on capital turnover.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Mapping

import httpx

BASE = "https://gateway.polymarket.us"
LEAGUES = ("nfl", "mlb", "nba", "nhl", "mls", "cbb", "cfb", "ufc", "ucl", "epl", "atp", "wta")


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _safe_market(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "slug", "marketSlug", "title", "name", "question", "active", "closed",
        "state", "status", "startTime", "endDate", "live", "ended", "period",
    )
    return {k: row.get(k) for k in keys if row.get(k) is not None}


def _event_row(league: str, raw: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    live = raw.get("live") is True
    ended = raw.get("ended") is True or raw.get("closed") is True
    if not live or ended:
        return None
    end = _dt(raw.get("endDate") or raw.get("end_time") or raw.get("endTime"))
    hours = None if end is None else round((end - now).total_seconds() / 3600, 3)
    markets = [
        _safe_market(x) for x in (raw.get("markets") or [])
        if isinstance(x, Mapping) and x.get("closed") is not True
    ]
    keys = (
        "id", "slug", "eventSlug", "title", "name", "description", "live", "ended",
        "period", "score", "startTime", "endDate", "active", "closed", "status",
    )
    out = {k: raw.get(k) for k in keys if raw.get(k) is not None}
    out.update({"league": league, "hours_to_end": hours, "markets": markets})
    return out


async def _league(http: httpx.AsyncClient, league: str) -> tuple[str, list[dict[str, Any]], str | None]:
    try:
        response = await http.get(
            f"{BASE}/v2/leagues/{league}/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
        response.raise_for_status()
        body = response.json()
        rows = body.get("events") or body.get("data") or [] if isinstance(body, dict) else []
        return league, [dict(x) for x in rows if isinstance(x, Mapping)], None
    except Exception as exc:
        return league, [], f"{type(exc).__name__}:{str(exc)[:180]}"


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=12.0) as http:
        results = await asyncio.gather(*(_league(http, league) for league in LEAGUES))
    live_rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    raw_counts: dict[str, int] = {}
    for league, rows, error in results:
        raw_counts[league] = len(rows)
        if error:
            errors[league] = error
        for raw in rows:
            row = _event_row(league, raw, now)
            if row is not None:
                live_rows.append(row)
    live_rows.sort(key=lambda x: (x.get("hours_to_end") is None, x.get("hours_to_end") or 1e9, x.get("league") or ""))
    under_24 = [x for x in live_rows if x.get("hours_to_end") is not None and -1 <= float(x["hours_to_end"]) <= 24]
    under_6 = [x for x in under_24 if float(x["hours_to_end"]) <= 6]
    return {
        "status": "POLYMARKET_LIVE_SPORTS_INVENTORY",
        "as_of_utc": now.isoformat(),
        "raw_active_events_by_league": raw_counts,
        "live_events": len(live_rows),
        "live_under_24h": len(under_24),
        "live_under_6h": len(under_6),
        "ranked_live_events": live_rows[:100],
        "errors": errors,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    print("POLYMARKET_LIVE_SPORTS_INVENTORY " + json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
