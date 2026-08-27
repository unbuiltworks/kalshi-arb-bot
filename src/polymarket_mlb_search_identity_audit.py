"""Read-only audit for Polymarket US MLB Search event identity reachability.

The strict MLB prop scanner intentionally requires an exact event slug match between
Polymarket's league inventory and `/v1/search`.  When strict counterpart discovery is
zero, we need to distinguish four very different states without relaxing identity:

* the exact league event is returned and has parser-reachable player props;
* the exact league event is returned but currently has no player props;
* Search returns similarly titled events but not the exact league slug (search identity drift);
* Search returns no plausible event at all.

This module is diagnostic only.  It never emits executable candidates and never falls
back from exact identity to fuzzy matching.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.polymarket_us_mlb_prop_supply_audit import audit_payload

PAST_GRACE = timedelta(hours=2)
FUTURE_HORIZON = timedelta(hours=18)
SEARCH_CONCURRENCY = 4


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _nearby(event: dict[str, Any], now: datetime) -> bool:
    start = _parse_utc(event.get("startTime"))
    return start is not None and now - PAST_GRACE <= start <= now + FUTURE_HORIZON


def audit_search_identity(
    payload: dict[str, Any], *, expected_event_slug: str, query: str = ""
) -> dict[str, Any]:
    """Classify whether Search exposes the exact event the strict scanner requires."""
    expected = str(expected_event_slug or "").strip()
    events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
    exact = [row for row in events if str(row.get("slug") or "").strip() == expected]

    if not expected:
        status = "MISSING_EXPECTED_EVENT_SLUG"
        exact_event = None
    elif len(exact) == 1:
        exact_event = exact[0]
        nested_audit = audit_payload({"events": [exact_event]}, query=query)
        if nested_audit["strict_parser_compatible_prop_rows"] > 0:
            status = "EXACT_EVENT_WITH_STRICT_PROP_SUPPLY"
        elif nested_audit["prop_like_rows"] > 0:
            status = "EXACT_EVENT_PROP_SCHEMA_DRIFT"
        else:
            status = "EXACT_EVENT_NO_PLAYER_PROP_SUPPLY"
    elif len(exact) > 1:
        exact_event = None
        status = "DUPLICATE_EXACT_EVENT_SLUG_AMBIGUOUS"
    elif events:
        exact_event = None
        status = "SEARCH_EVENT_SLUG_DRIFT_OR_MISSING_EXACT_EVENT"
    else:
        exact_event = None
        status = "SEARCH_RETURNED_NO_EVENTS"

    event_samples = [
        {
            "slug": row.get("slug"),
            "title": row.get("title") or row.get("name"),
            "start_time": row.get("startTime"),
            "market_count": len(row.get("markets") or []) if isinstance(row.get("markets"), list) else None,
        }
        for row in events[:10]
    ]

    out: dict[str, Any] = {
        "query": query,
        "expected_event_slug": expected,
        "search_event_count": len(events),
        "exact_event_match_count": len(exact),
        "status": status,
        "event_samples": event_samples,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }
    if exact_event is not None:
        supply = audit_payload({"events": [exact_event]}, query=query)
        out.update(
            {
                "exact_event_title": exact_event.get("title") or exact_event.get("name"),
                "exact_event_start_time": exact_event.get("startTime"),
                "prop_like_rows": supply["prop_like_rows"],
                "strict_parser_compatible_prop_rows": supply["strict_parser_compatible_prop_rows"],
                "parser_drift_prop_rows": supply["parser_drift_prop_rows"],
                "parser_drift_reason_counts": supply["parser_drift_reason_counts"],
            }
        )
    return out


async def _fetch_json(http: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
    response = await http.get(url, params=params)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def run_live() -> dict[str, Any]:
    """Audit nearby MLB league events against the exact Search identity contract."""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as http:
        league = await _fetch_json(
            http,
            f"{base.POLY}/v2/leagues/mlb/events",
            params={"limit": 1000, "active": "true", "closed": "false"},
        )
        now = datetime.now(timezone.utc)
        nearby = [
            dict(event)
            for event in (league.get("events") or [])
            if isinstance(event, dict) and _nearby(event, now) and str(event.get("slug") or "").strip()
        ]
        semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)

        async def one(event: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                query = str(event.get("title") or event.get("slug") or "")
                body = await _fetch_json(
                    http,
                    f"{base.POLY}/v1/search",
                    params={"query": query, "limit": 100, "status": "active", "page": 1},
                )
            return audit_search_identity(
                body,
                expected_event_slug=str(event.get("slug") or ""),
                query=query,
            )

        reports = await asyncio.gather(*(one(event) for event in nearby))

    status_counts: dict[str, int] = {}
    for report in reports:
        status = str(report["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "audit": "POLYMARKET_US_MLB_SEARCH_IDENTITY",
        "nearby_league_events": len(nearby),
        "status_counts": status_counts,
        "reports": reports,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }


def main() -> int:
    print(json.dumps(asyncio.run(run_live()), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
