"""One-shot discovery-only Gemini <-> Kalshi MLB player-prop inventory probe.

Fetches current public MLB prop catalogs, runs the existing exact identity join, and
prints counterpart inventory. It performs no book pricing, participation lookup,
authenticated market data, alerts, or order entry.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json

import httpx

from src.gemini_kalshi_mlb_prop_shadow import _candidate_sides
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.universal_three_venue_sports import fetch_gemini_mlb


async def run_once() -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http),
            _fetch_kalshi_events(http),
        )

    _sides, venues_by_key, joins, _rule_context = _candidate_sides(
        kalshi_events, gemini_events
    )
    by_family = Counter(str(row.get("family") or "") for row in joins)
    game_rows: dict[tuple[str, ...], dict[str, object]] = {}
    for row in joins:
        teams = tuple(sorted(str(x) for x in (row.get("teams") or [])))
        bucket = game_rows.setdefault(teams, {
            "teams": list(teams),
            "join_count": 0,
            "families": Counter(),
            "players": set(),
            "samples": [],
        })
        bucket["join_count"] = int(bucket["join_count"]) + 1
        bucket["families"][str(row.get("family") or "")] += 1
        bucket["players"].add(str(row.get("player") or ""))
        if len(bucket["samples"]) < 8:
            bucket["samples"].append({
                "player": row.get("player"),
                "stat": row.get("stat"),
                "threshold": row.get("threshold"),
                "kalshi_ticker": row.get("kalshi_ticker"),
                "gemini_market_id": row.get("gemini_market_id"),
                "start_skew_ms": row.get("start_skew_ms"),
            })

    games = []
    for bucket in game_rows.values():
        games.append({
            "teams": bucket["teams"],
            "join_count": bucket["join_count"],
            "families": dict(bucket["families"]),
            "player_count": len(bucket["players"]),
            "samples": bucket["samples"],
        })
    games.sort(key=lambda row: int(row["join_count"]), reverse=True)

    return {
        "status": "DIRECT_GK_MLB_PROP_INVENTORY",
        "gemini_events": len(gemini_events),
        "kalshi_prop_events": len(kalshi_events),
        "exact_join_rows": len(joins),
        "exact_join_keys": len(venues_by_key),
        "joins_by_family": dict(by_family),
        "games_with_joins": len(games),
        "games": games,
        "read_only": True,
        "order_entry": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
