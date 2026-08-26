"""Read-only K<->P MLB scheduled-start alignment diagnostic.

This module exists only to diagnose exact semantic MLB game keys that fail the
existing <=5 minute scheduled-start identity gate. It never prices or submits
orders and must not be used to relax that gate. The output reports the start
skew distribution plus the raw venue timestamp fields used by the adapters so
we can correct only a proven normalization bug.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable, Mapping

import httpx

from src.mlb_game_side_normalization import (
    NormalizedSide,
    normalize_kalshi_mlb,
    normalize_polymarket_mlb,
)
from src.sports_kp_generic_mlb_game_breadth import (
    MAX_START_SKEW_MS,
    POLY_HEADERS,
    _distinct_physical_sides,
    _fetch_inputs,
)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _raw_poly_start_fields(event: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "market_gameStartTime": market.get("gameStartTime"),
        "event_startTime": event.get("startTime"),
        "event_startDate": event.get("startDate"),
        "market_startDate": market.get("startDate"),
    }


def _raw_kalshi_start_fields(event: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_ticker": event.get("event_ticker"),
        "event_strike_date": event.get("strike_date"),
        "market_expected_expiration_time": market.get("expected_expiration_time"),
        "market_close_time": market.get("close_time"),
    }


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[idx]


def _raw_indexes(
    poly_events: Iterable[Mapping[str, Any]], kalshi_events: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]], dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]]:
    p: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for event in poly_events:
        for market in event.get("markets") or []:
            if isinstance(market, Mapping):
                slug = str(market.get("slug") or market.get("id") or "")
                if slug:
                    p[slug] = (event, market)
    k: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for event in kalshi_events:
        for market in event.get("markets") or []:
            if isinstance(market, Mapping):
                ticker = str(market.get("ticker") or "")
                if ticker:
                    k[ticker] = (event, market)
    return p, k


def build_start_skew_report(
    poly_events: list[Mapping[str, Any]],
    kalshi_events: list[Mapping[str, Any]],
    *,
    sample_limit: int = 12,
) -> dict[str, Any]:
    poly_sides = normalize_polymarket_mlb(poly_events)
    kalshi_sides = normalize_kalshi_mlb(kalshi_events)
    pidx: dict[str, list[NormalizedSide]] = defaultdict(list)
    kidx: dict[str, list[NormalizedSide]] = defaultdict(list)
    for row in poly_sides:
        pidx[row.contract_key].append(row)
    for row in kalshi_sides:
        kidx[row.contract_key].append(row)

    raw_poly, raw_kalshi = _raw_indexes(poly_events, kalshi_events)
    shared = sorted(set(pidx) & set(kidx))
    per_key: list[dict[str, Any]] = []
    for key in shared:
        prows = _distinct_physical_sides(pidx[key])
        krows = _distinct_physical_sides(kidx[key])
        combos: list[tuple[int, NormalizedSide, NormalizedSide]] = []
        for pr in prows:
            for kr in krows:
                skew = abs(pr.identity.event.scheduled_start_ms - kr.identity.event.scheduled_start_ms)
                combos.append((skew, pr, kr))
        if not combos:
            continue
        skew, pr, kr = min(combos, key=lambda x: x[0])
        pe, pm = raw_poly.get(pr.venue_market_id, ({}, {}))
        ke, km = raw_kalshi.get(kr.venue_market_id, ({}, {}))
        per_key.append({
            "contract_key": key,
            "family": pr.identity.market_family,
            "poly_market": pr.venue_market_id,
            "kalshi_market": kr.venue_market_id,
            "poly_normalized_start": _iso(pr.identity.event.scheduled_start_ms),
            "kalshi_normalized_start": _iso(kr.identity.event.scheduled_start_ms),
            "start_skew_ms": skew,
            "passes_5m_gate": skew <= MAX_START_SKEW_MS,
            "poly_raw_start_fields": _raw_poly_start_fields(pe, pm),
            "kalshi_raw_start_fields": _raw_kalshi_start_fields(ke, km),
        })

    skews = [int(row["start_skew_ms"]) for row in per_key]
    rejected = [row for row in per_key if not row["passes_5m_gate"]]
    rejected.sort(key=lambda row: int(row["start_skew_ms"]))
    return {
        "classification": "READ_ONLY_TIMESTAMP_ALIGNMENT_DIAGNOSTIC_NOT_EXECUTION_ELIGIBLE",
        "read_only": True,
        "order_entry": False,
        "max_start_skew_ms_gate": MAX_START_SKEW_MS,
        "poly_events": len(poly_events),
        "kalshi_events": len(kalshi_events),
        "shared_semantic_keys": len(shared),
        "keys_with_start_measurement": len(per_key),
        "keys_passing_5m_gate": sum(1 for row in per_key if row["passes_5m_gate"]),
        "keys_rejected_start_skew": len(rejected),
        "start_skew_ms": {
            "min": min(skews) if skews else None,
            "p50": _percentile(skews, 0.50),
            "p90": _percentile(skews, 0.90),
            "p99": _percentile(skews, 0.99),
            "max": max(skews) if skews else None,
        },
        "samples": rejected[:sample_limit],
    }


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=15.0, headers=POLY_HEADERS) as http:
        poly_events, kalshi_events, _ = await _fetch_inputs(http)
    report = build_start_skew_report(poly_events, kalshi_events)
    print("SPORTS_KP_MLB_START_SKEW " + json.dumps(report, separators=(",", ":"), default=str), flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
