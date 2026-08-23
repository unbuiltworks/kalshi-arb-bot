"""Read-only stage-by-stage Gemini/Kalshi MLB prop join diagnostics.

This module explains *why* public Gemini and Kalshi MLB prop universes fail to
produce exact join rows. It never imports order transport and cannot certify or
authorize execution.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_mlb_prop_shadow import (
    FAMILY_TO_GEMINI_STAT,
    MAX_START_SKEW_MS,
    SERIES_TO_FAMILY,
    _kalshi_teams,
)
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.strict_mlb_prop_adapters import _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc
from src.strict_mlb_prop_snapshot import _fetch_kalshi_events
from src.three_venue_mlb_prop_safe_scan import _discrete_threshold_and_side
from src.universal_three_venue_sports import fetch_gemini_mlb


def build_join_funnel(
    kalshi_events: list[dict[str, Any]],
    gemini_events: list[dict[str, Any]],
) -> dict[str, Any]:
    gemini_props = [
        row for row in normalize_gemini_mlb(gemini_events)
        if row.identity.market_family == "player_prop"
    ]

    stage = Counter()
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    stage["gemini_player_props"] = len(gemini_props)
    stage["kalshi_events"] = len(kalshi_events)

    for event in kalshi_events:
        series = str(event.get("series_ticker") or "")
        family = SERIES_TO_FAMILY.get(series)
        if family is None:
            stage["kalshi_events_unsupported_family"] += 1
            continue
        by_family[family]["kalshi_events"] += 1
        stage["kalshi_events_supported_family"] += 1

        stat = FAMILY_TO_GEMINI_STAT.get(family)
        if stat is None:
            stage["kalshi_events_missing_gemini_stat_map"] += 1
            by_family[family]["missing_gemini_stat_map"] += 1
            continue
        stage["kalshi_events_with_gemini_stat_map"] += 1
        by_family[family]["with_gemini_stat_map"] += 1

        teams = _kalshi_teams(event)
        if teams is None:
            stage["kalshi_events_team_decode_failed"] += 1
            by_family[family]["team_decode_failed"] += 1
            continue
        stage["kalshi_events_team_decoded"] += 1
        by_family[family]["team_decoded"] += 1

        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            stage["kalshi_markets_seen"] += 1
            by_family[family]["kalshi_markets_seen"] += 1
            parsed = _kalshi_identity(event, market, family)
            if parsed is None:
                stage["kalshi_markets_identity_parse_failed"] += 1
                by_family[family]["identity_parse_failed"] += 1
                continue
            player, threshold = parsed
            subject = _name(player)
            start = kalshi_event_start_utc(event, market)
            if start is None:
                stage["kalshi_markets_missing_start"] += 1
                by_family[family]["missing_start"] += 1
                continue
            start_ms = int(start.timestamp() * 1000)
            stage["kalshi_markets_parsed"] += 1
            by_family[family]["parsed_markets"] += 1

            stat_player = [
                grow for grow in gemini_props
                if grow.identity.stat == stat and _name(grow.identity.subject) == subject
            ]
            if not stat_player:
                stage["no_stat_player_match"] += 1
                by_family[family]["no_stat_player_match"] += 1
                continue
            stage["has_stat_player_match"] += 1
            by_family[family]["has_stat_player_match"] += 1

            team_match = [
                grow for grow in stat_player
                if set(grow.identity.event.participants) == set(teams)
            ]
            if not team_match:
                stage["no_team_match"] += 1
                by_family[family]["no_team_match"] += 1
                continue
            stage["has_team_match"] += 1
            by_family[family]["has_team_match"] += 1

            time_match = [
                grow for grow in team_match
                if abs(grow.identity.event.scheduled_start_ms - start_ms) <= MAX_START_SKEW_MS
            ]
            if not time_match:
                stage["no_start_time_match"] += 1
                by_family[family]["no_start_time_match"] += 1
                continue
            stage["has_start_time_match"] += 1
            by_family[family]["has_start_time_match"] += 1

            discrete = []
            for grow in time_match:
                mapped = _discrete_threshold_and_side(grow.identity.comparator, grow.identity.line)
                if mapped is not None:
                    discrete.append((grow, mapped))
            if not discrete:
                stage["no_discrete_mapping"] += 1
                by_family[family]["no_discrete_mapping"] += 1
                continue
            stage["has_discrete_mapping"] += 1
            by_family[family]["has_discrete_mapping"] += 1

            exact = [
                (grow, mapped) for grow, mapped in discrete
                if mapped[0] == threshold
            ]
            if not exact:
                stage["threshold_mismatch"] += 1
                by_family[family]["threshold_mismatch"] += 1
                if len(examples[family]) < 5:
                    examples[family].append({
                        "reason": "threshold_mismatch",
                        "player": player,
                        "kalshi_threshold": str(threshold),
                        "gemini_thresholds": sorted({str(mapped[0]) for _, mapped in discrete}),
                        "teams": list(teams),
                    })
                continue

            stage["exact_join_markets"] += 1
            by_family[family]["exact_join_markets"] += 1
            if len(examples[family]) < 5:
                examples[family].append({
                    "reason": "exact_join",
                    "player": player,
                    "threshold": str(threshold),
                    "teams": list(teams),
                    "gemini_market_ids": sorted({grow.venue_market_id for grow, _ in exact}),
                    "kalshi_ticker": str(market.get("ticker") or ""),
                })

    family_rows = {
        family: {
            **{key: int(value) for key, value in counts.items()},
            "examples": examples.get(family, []),
        }
        for family, counts in sorted(by_family.items())
    }
    return {
        "diagnostic_only": True,
        "read_only": True,
        "order_entry": False,
        "certifies_execution": False,
        "stage_counts": {key: int(value) for key, value in stage.items()},
        "families": family_rows,
        "missing_gemini_stat_families": sorted(
            family for family in set(SERIES_TO_FAMILY.values())
            if FAMILY_TO_GEMINI_STAT.get(family) is None
        ),
    }


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_events = await asyncio.gather(
            fetch_gemini_mlb(http), _fetch_kalshi_events(http)
        )
    payload = build_join_funnel(kalshi_events, gemini_events)
    payload["gemini_events"] = len(gemini_events)
    payload["kalshi_prop_events"] = len(kalshi_events)
    print(
        "MLB_GK_FINAL_LAG_JOIN_FUNNEL "
        + json.dumps(payload, separators=(",", ":"), default=str),
        flush=True,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
