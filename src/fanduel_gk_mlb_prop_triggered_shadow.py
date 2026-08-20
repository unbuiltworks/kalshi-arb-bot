"""Triggered FanDuel vs direct Gemini/Kalshi MLB prop shadow scan.

Polls public Gemini/Kalshi prop inventory without using sportsbook quota.  Only after
an exact G/K game+player+stat+threshold join exists does it query The Odds API for
FanDuel props on the matching game(s).  It then prices only prediction keys that
also have an exact FanDuel leg and reports shadow economics.

One trigger batch only. No Pushover and no order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Mapping

import httpx

from src.fanduel_prediction_mlb_prop_shadow import (
    BOOKMAKER,
    EVENTS_URL,
    EVENT_ODDS_URL,
    REQUEST_MARKETS,
    MAX_START_SKEW_MS,
    _event_identity,
    _future_events,
    evaluate,
    normalize_fanduel_event,
)
from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_prop_safe_scan import _price_sides
from src.universal_three_venue_sports import fetch_gemini_mlb


def _matching_odds_events(
    sides: list[Any], future: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    targets: set[tuple[tuple[str, str], int]] = set()
    for side in sides:
        teams = tuple(sorted(str(x) for x in side.identity.event.participants))
        if len(teams) == 2:
            targets.add((teams, int(side.identity.event.scheduled_start_ms)))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in future:
        identity = _event_identity(event)
        event_id = str(event.get("id") or "")
        if identity is None or not event_id or event_id in seen:
            continue
        teams = tuple(sorted(str(x) for x in identity.participants))
        if any(
            teams == target_teams and abs(identity.scheduled_start_ms - target_start) <= MAX_START_SKEW_MS
            for target_teams, target_start in targets
        ):
            seen.add(event_id)
            out.append(event)
    return out


async def discovery(http: httpx.AsyncClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], list[dict[str, Any]]]:
    gemini_events, kalshi_events = await asyncio.gather(
        fetch_gemini_mlb(http),
        _fetch_kalshi_events(http),
    )
    sides, _venues_by_key, joins, _rule_context = _candidate_sides(kalshi_events, gemini_events)
    return gemini_events, kalshi_events, sides, joins


async def trigger_batch(api_key: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events, sides, joins = await discovery(http)
        if not joins:
            return None

        future, events_quota = await _future_events(http, api_key)
        matched_events = _matching_odds_events(sides, future)
        if not matched_events:
            return {
                "status": "FANDUEL_GK_TRIGGERED_NO_ODDS_EVENT_MATCH",
                "exact_gk_join_rows": len(joins),
                "exact_gk_keys": len({side.contract_key for side in sides}),
                "matched_odds_events": 0,
                "events_endpoint_quota": events_quota,
                "read_only": True,
                "order_entry": False,
            }

        fd_legs = []
        prop_quota_rows: list[dict[str, str]] = []
        event_audit: list[dict[str, Any]] = []
        for event in matched_events:
            event_id = str(event.get("id") or "")
            response = await http.get(
                EVENT_ODDS_URL.format(event_id=event_id),
                params={
                    "apiKey": api_key,
                    "bookmakers": BOOKMAKER,
                    "markets": ",".join(REQUEST_MARKETS),
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
            )
            response.raise_for_status()
            body = response.json()
            legs = normalize_fanduel_event(body if isinstance(body, Mapping) else {})
            fd_legs.extend(legs)
            prop_quota_rows.append({
                "event_id": event_id,
                "remaining": response.headers.get("x-requests-remaining", ""),
                "used": response.headers.get("x-requests-used", ""),
                "last_cost": response.headers.get("x-requests-last", ""),
            })
            event_audit.append({
                "event_id": event_id,
                "away_team": event.get("away_team"),
                "home_team": event.get("home_team"),
                "commence_time": event.get("commence_time"),
                "fanduel_legs": len(legs),
            })

        fd_keys = {leg.settlement_key for leg in fd_legs}
        filtered_sides = [side for side in sides if side.contract_key in fd_keys]
        prediction_rows, errors = await _price_sides(
            http, filtered_sides, gemini_events, kalshi_events
        )

    key_start_ms: dict[str, int] = {}
    for side in filtered_sides:
        key = side.contract_key
        start = int(side.identity.event.scheduled_start_ms)
        existing = key_start_ms.get(key)
        if existing is None:
            key_start_ms[key] = start
        elif abs(existing - start) > MAX_START_SKEW_MS:
            key_start_ms[key] = -1

    economics = evaluate(fd_legs, prediction_rows, key_start_ms)
    return {
        "status": "FANDUEL_GK_TRIGGERED_SHADOW",
        "exact_gk_join_rows": len(joins),
        "exact_gk_keys": len({side.contract_key for side in sides}),
        "joins_by_family": dict(Counter(str(row.get("family") or "") for row in joins)),
        "matched_odds_events": len(matched_events),
        "event_audit": event_audit,
        "events_endpoint_quota": events_quota,
        "prop_quota": prop_quota_rows,
        "fanduel_total_legs": len(fd_legs),
        "fanduel_exact_keys": len(fd_keys),
        "prediction_sides_selected_for_pricing": len(filtered_sides),
        "prediction_priced_quotes": len(prediction_rows),
        "prediction_priced_by_venue": dict(Counter(row.quote.venue for row in prediction_rows)),
        "prediction_errors": errors[:20],
        **economics,
        "rule_gate": "COMPLETE_CROSS_VENUE_PROP_LIFECYCLE_CERTIFICATION_REQUIRED",
        "alert_eligible": False,
        "execution_eligible": False,
        "realized_pnl": "0",
        "read_only": True,
        "order_entry": False,
    }


async def supervise(api_key: str, poll_seconds: float) -> None:
    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            result = await trigger_batch(api_key)
            if result is None:
                print(json.dumps({
                    "status": "FANDUEL_GK_TRIGGER_WAITING",
                    "cycle": cycle,
                    "odds_api_prop_calls": 0,
                    "reason": "NO_EXACT_GK_PROP_JOINS",
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")), flush=True)
            else:
                print(json.dumps({"cycle": cycle, **result}, separators=(",", ":"), default=str), flush=True)
                return
        except Exception as exc:
            print(json.dumps({
                "status": "FANDUEL_GK_TRIGGER_ERROR",
                "cycle": cycle,
                "error": f"{type(exc).__name__}:{str(exc)[:300]}",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5.0, poll_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=float, default=120.0)
    args = parser.parse_args()
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY required")
    asyncio.run(supervise(api_key, max(30.0, args.poll_seconds)))


if __name__ == "__main__":
    main()
