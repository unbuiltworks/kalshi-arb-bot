"""Broad read-only Polymarket US sports inventory: LIVE + next-24h PREGAME.

The existing live inventory intentionally excludes future events. Maker resting
orders benefit from discovering/certifying ladders before the contest begins, so
this module pages the active/open/not-ended sports universe through a future
horizon, then classifies every retained event as LIVE or PREGAME_NEAR_TERM.

No authentication, market creation, order entry, or capital use.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

import httpx

from src.sports_live_poly_inventory import BASE, PAGE, MAX_PAGES, _event_row, _live_reason

HORIZON_HOURS = 24


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _event_start(event: Mapping[str, Any]) -> datetime | None:
    return _parse_time(event.get("startTime") or event.get("startDate") or event.get("eventDate"))


def _classify(event: Mapping[str, Any], *, now: datetime, horizon: datetime) -> str | None:
    live = _live_reason(event)
    if live:
        return "LIVE"
    start = _event_start(event)
    if start is not None and now <= start <= horizon:
        return "PREGAME_NEAR_TERM"
    return None


async def _fetch_page(http: httpx.AsyncClient, *, offset: int, horizon_iso: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "limit": PAGE,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "ended": "false",
        "categories": "sports",
        "startTimeMax": horizon_iso,
        "orderBy": "startTime",
        "orderDirection": "desc",
    }
    try:
        response = await http.get(f"{BASE}/v1/events", params=params)
        status = response.status_code
        response.raise_for_status()
        body = response.json()
        rows = body.get("events") or [] if isinstance(body, dict) else []
        rows = [dict(row) for row in rows if isinstance(row, Mapping)]
        return rows, {"offset": offset, "status": status, "rows": len(rows)}
    except Exception as exc:
        return [], {"offset": offset, "status": None, "rows": 0, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HORIZON_HOURS)
    horizon_iso = horizon.isoformat().replace("+00:00", "Z")
    diagnostics: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as http:
        for page in range(MAX_PAGES):
            rows, diag = await _fetch_page(http, offset=page * PAGE, horizon_iso=horizon_iso)
            diagnostics.append(diag)
            if diag.get("error"):
                break
            for row in rows:
                key = str(row.get("id") or row.get("gameId") or row.get("slug") or "")
                if key and key not in seen:
                    seen.add(key)
                    raw_events.append(row)
            if len(rows) < PAGE:
                break

    retained: list[dict[str, Any]] = []
    for event in raw_events:
        phase = _classify(event, now=now, horizon=horizon)
        if phase is None:
            continue
        live_reason = _live_reason(event)
        row = _event_row(event, live_reason or "PREGAME_NEAR_TERM")
        row["phase"] = phase
        retained.append(row)

    retained.sort(key=lambda row: (str(row.get("start_time") or ""), str(row.get("series_slug") or row.get("subcategory") or ""), str(row.get("slug") or "")))
    phase_counts = Counter(str(row.get("phase") or "UNKNOWN") for row in retained)
    series_counts = Counter(str(row.get("series_slug") or row.get("subcategory") or row.get("category") or "unknown") for row in retained)
    phase_series_counts = Counter(f"{row.get('phase') or 'UNKNOWN'}|{row.get('series_slug') or row.get('subcategory') or row.get('category') or 'unknown'}" for row in retained)

    return {
        "status": "POLYMARKET_US_NEAR_TERM_SPORTS_INVENTORY_V1",
        "observed_at": now.isoformat(),
        "horizon_hours": HORIZON_HOURS,
        "horizon": horizon.isoformat(),
        "active_open_not_ended_scanned": len(raw_events),
        "event_count": len(retained),
        "live_event_count": phase_counts.get("LIVE", 0),
        "pregame_event_count": phase_counts.get("PREGAME_NEAR_TERM", 0),
        "phase_counts": dict(phase_counts),
        "series_counts": dict(series_counts.most_common()),
        "phase_series_counts": dict(phase_series_counts.most_common()),
        "events_with_open_markets": sum(row["open_market_count"] > 0 for row in retained),
        "events_with_accepting_markets": sum(row["accepting_market_count"] > 0 for row in retained),
        "diagnostics": diagnostics,
        "events": retained[:750],
        "read_only": True,
        "market_creation": False,
        "order_entry": False,
        "funds_at_risk": False,
    }


def main() -> None:
    print("POLYMARKET_US_NEAR_TERM_SPORTS_INVENTORY_V1 " + json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
