"""Read-only live rejection-stage diagnostics for strict MLB prop matching.

This module never prices or enters orders. It uses the same Polymarket /v1/search
player-prop discovery as the live shadow collector and reports the exact identity /
schedule gate where otherwise plausible Polymarket US <-> Kalshi counterparts stop.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import logging
from typing import Any

import httpx

from src.strict_mlb_prop_adapters import FAMILY_SERIES, _kalshi_identity, _name, _poly_identity
from src.strict_mlb_prop_schedule_guard import MAX_START_SKEW_SECONDS, kalshi_event_start_utc, polymarket_start_utc
from src.strict_mlb_prop_snapshot import KALSHI, _fetch_json
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_live_diagnostic")


def _safe_poly_sample(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": row.get("sportsMarketType"),
        "slug": row.get("slug"),
        "question": row.get("question") or row.get("title"),
        "line": row.get("line"),
        "gameStartTime": row.get("gameStartTime"),
        "eventStartTime": row.get("eventStartTime"),
    }


def _safe_kalshi_sample(event: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    return {
        "series_ticker": event.get("series_ticker") or market.get("series_ticker"),
        "event_ticker": event.get("event_ticker") or market.get("event_ticker"),
        "event_title": event.get("title") or event.get("sub_title"),
        "event_occurrence_datetime": event.get("occurrence_datetime"),
        "market_occurrence_datetime": market.get("occurrence_datetime"),
        "market_ticker": market.get("ticker"),
        "market_title": market.get("title"),
        "yes_sub_title": market.get("yes_sub_title"),
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=35.0) as http:
        poly_rows = await _fetch_poly_mlb_search(http)

        kalshi_groups: dict[str, list[dict[str, Any]]] = {}
        for series in sorted(set(FAMILY_SERIES.values())):
            body = await _fetch_json(
                http,
                f"{KALSHI}/events",
                params={
                    "limit": 200,
                    "status": "open",
                    "series_ticker": series,
                    "with_nested_markets": "true",
                },
            )
            kalshi_groups[series] = list(body.get("events") or [])

    poly_identity_valid = [row for row in poly_rows if _poly_identity(row) is not None]
    poly_start_valid = [row for row in poly_identity_valid if polymarket_start_utc(row) is not None]
    poly_by_family = Counter(str(row.get("sportsMarketType") or "") for row in poly_rows)
    poly_identity_by_family = Counter(str(row.get("sportsMarketType") or "") for row in poly_identity_valid)
    poly_start_by_family = Counter(str(row.get("sportsMarketType") or "") for row in poly_start_valid)

    kalshi_stats: dict[str, dict[str, int]] = {}
    kalshi_examples: list[dict[str, Any]] = []
    for family, series in FAMILY_SERIES.items():
        events = kalshi_groups.get(series, [])
        market_count = 0
        identity_count = 0
        start_count = 0
        for event in events:
            for market in event.get("markets") or []:
                market_count += 1
                if _kalshi_identity(event, market, family) is not None:
                    identity_count += 1
                if kalshi_event_start_utc(event, market) is not None:
                    start_count += 1
                if len(kalshi_examples) < 12:
                    kalshi_examples.append(_safe_kalshi_sample(event, market))
        kalshi_stats[family] = {
            "events": len(events),
            "markets": market_count,
            "identity_valid_markets": identity_count,
            "start_valid_markets": start_count,
        }

    gates = Counter()
    nearest: list[dict[str, Any]] = []
    exact_identity_candidates: list[dict[str, Any]] = []
    for poly in poly_identity_valid:
        p = _poly_identity(poly)
        assert p is not None
        family, player, teams, game_date, threshold = p
        p_start = polymarket_start_utc(poly)
        if p_start is None:
            gates["poly_start_missing"] += 1
            continue
        expected_series = FAMILY_SERIES[family]
        for event in kalshi_groups.get(expected_series, []):
            for market in event.get("markets") or []:
                k = _kalshi_identity(event, market, family)
                if k is None:
                    continue
                gates["kalshi_identity_valid"] += 1
                k_player, k_threshold = k
                if _name(player) != _name(k_player):
                    continue
                gates["player_match"] += 1
                if threshold != k_threshold:
                    continue
                gates["threshold_match"] += 1

                ticker = str(market.get("ticker") or "")
                event_ticker = str(event.get("event_ticker") or market.get("event_ticker") or "").upper()
                a, b = (x.upper() for x in teams)
                team_ok = event_ticker.endswith(a + b) or event_ticker.endswith(b + a)
                if not team_ok:
                    exact_identity_candidates.append({
                        "failure": "team_suffix",
                        "family": family,
                        "player": player,
                        "threshold": str(threshold),
                        "poly_slug": poly.get("slug"),
                        "kalshi_ticker": ticker,
                        "kalshi_event_ticker": event_ticker,
                        "poly_teams": list(teams),
                    })
                    continue
                gates["team_match"] += 1

                k_start = kalshi_event_start_utc(event, market)
                if k_start is None:
                    gates["kalshi_start_missing_after_identity"] += 1
                    continue
                gates["both_starts_present"] += 1
                skew = abs((p_start - k_start).total_seconds())
                row = {
                    "family": family,
                    "player": player,
                    "threshold": str(threshold),
                    "poly_slug": poly.get("slug"),
                    "kalshi_ticker": ticker,
                    "game_date": game_date,
                    "poly_start_utc": p_start.isoformat(),
                    "kalshi_start_utc": k_start.isoformat(),
                    "start_skew_seconds": skew,
                }
                nearest.append(row)
                if skew <= MAX_START_SKEW_SECONDS:
                    gates["schedule_match"] += 1
                    exact_identity_candidates.append({"failure": None, **row})

    nearest.sort(key=lambda row: row["start_skew_seconds"])
    result = {
        "poly_supported_markets": len(poly_rows),
        "poly_identity_valid": len(poly_identity_valid),
        "poly_start_valid": len(poly_start_valid),
        "poly_by_family": dict(poly_by_family),
        "poly_identity_by_family": dict(poly_identity_by_family),
        "poly_start_by_family": dict(poly_start_by_family),
        "kalshi_by_family": kalshi_stats,
        "cross_match_gates": dict(gates),
        "exact_identity_candidates": exact_identity_candidates[:20],
        "nearest_schedule_candidates": nearest[:20],
        "poly_examples": [_safe_poly_sample(row) for row in poly_rows[:12]],
        "kalshi_examples": kalshi_examples,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("STRICT_MLB_PROP_DIAGNOSTIC %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
