"""Read-only Kalshi <-> Polymarket MLB full-game edge census.

Uses the existing exact full-game normalization and current fee-inclusive q=1 pricing
stack. Kalshi prices come from nested event snapshots, so this broad census does not
consume an authenticated Kalshi WebSocket connection or per-market orderbook calls.

This measures incidence/depth only. Because it is repeated REST/event-snapshot data,
it must not be used to claim sub-second opportunity lifetimes. No credentials or
order-entry code are used.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import os
import time
from typing import Any

import httpx

from src.mlb_game_side_normalization import normalize_polymarket_mlb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.polymarket_scope_guard import full_game_only_events
from src.three_venue_mlb_price_scan import _dedupe
from src.three_venue_mlb_snapshot_pricing import price_counterparts_snapshot
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import counterpart_clusters, fetch_kalshi_mlb, fetch_polymarket_mlb

D = Decimal
THRESHOLDS = (D("0.01"), D("0.02"), D("0.03"))
INTERVAL_S = max(5.0, min(120.0, float(os.getenv("SPORTS_KP_CENSUS_INTERVAL_S", "15"))))


def _emit(stage: str, payload: dict[str, Any]) -> None:
    print(stage + " " + json.dumps(payload, separators=(",", ":"), default=str), flush=True)


def _rows_for_threshold(quotes, threshold: D):
    locks = _dedupe(find_cross_venue_locks(
        [row.quote for row in quotes if row.quote.venue in {"kalshi", "polymarket"}],
        min_unit_profit=threshold,
        max_quote_skew_ms=5000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    return [row for row in locks if {row.yes_venue, row.no_venue} == {"kalshi", "polymarket"}]


async def run_once() -> dict[str, Any]:
    started = time.monotonic()
    snapshot_ts_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly_raw, kalshi = await asyncio.gather(fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        poly = full_game_only_events(poly_raw)
        pnorm = normalize_polymarket_mlb(poly)
        knorm = normalize_kalshi_mlb_eastern(kalshi)
        clusters = counterpart_clusters(pnorm + knorm)
        priced, errors, key_count = await price_counterparts_snapshot(http, [], poly, kalshi)

    kp_quotes = [row for row in priced if row.quote.venue in {"kalshi", "polymarket"}]
    by_threshold = {str(t): _rows_for_threshold(kp_quotes, t) for t in THRESHOLDS}
    top = []
    for row in by_threshold["0.01"][:30]:
        item = row.as_dict()
        item["edge_key"] = "|".join((row.settlement_key, row.yes_venue, row.no_venue))
        item["passes_1c"] = row.unit_profit >= D("0.01")
        item["passes_2c"] = row.unit_profit >= D("0.02")
        item["passes_3c"] = row.unit_profit >= D("0.03")
        item["visible_capacity_note"] = "top-of-book q=1 fee economics; max_quantity is visible matched depth at observed top prices"
        top.append(item)

    kp_clusters = [r for r in clusters if set(r.get("venues") or []) == {"kalshi", "polymarket"}]
    out = {
        "status": "KALSHI_POLYMARKET_FULL_GAME_EDGE_CENSUS",
        "snapshot_ts_ms": snapshot_ts_ms,
        "scan_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "raw_events": {"polymarket": len(poly_raw), "kalshi": len(kalshi)},
        "normalized_sides": {"polymarket": len(pnorm), "kalshi": len(knorm)},
        "kp_counterpart_clusters": len(kp_clusters),
        "kp_clusters_by_family": dict(Counter(r.get("market_family") for r in kp_clusters)),
        "normal_event_counterpart_keys": key_count,
        "priced_kp_quotes": len(kp_quotes),
        "priced_kp_quotes_by_venue": dict(Counter(r.quote.venue for r in kp_quotes)),
        "taker_locked_1c_candidates": len(by_threshold["0.01"]),
        "taker_locked_2c_candidates": len(by_threshold["0.02"]),
        "taker_locked_3c_candidates": len(by_threshold["0.03"]),
        "top_1c": top,
        "fetch_error_count": len(errors),
        "fetch_errors": errors[:20],
        "families": ["moneyline", "spread", "total"],
        "scope_gate": "POLYMARKET_MAIN_FULL_GAME_ONLY",
        "time_gate": "KALSHI_TICKER_AMERICA_NEW_YORK_CORROBORATED_BY_EXPECTED_EXPIRY",
        "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "settlement_certified_for_final_capacity": False,
        "capacity_note": "do not sum overlapping settlement keys/shared book depth as independent final daily capacity",
        "lifetime_measurement": "NOT_FROM_REPEATED_REST_SNAPSHOT",
        "authenticated_kalshi_ws_used": False,
        "capital_reserved": False,
        "order_entry": False,
        "realized": False,
    }
    _emit("SPORTS_KP_EDGE_CENSUS_SNAPSHOT", out)
    return out


async def run_forever() -> None:
    cycle = 0
    while True:
        cycle += 1
        t0 = time.monotonic()
        try:
            out = await run_once()
            _emit("SPORTS_KP_EDGE_CENSUS_HEARTBEAT", {
                "cycle": cycle,
                "snapshot_ts_ms": out["snapshot_ts_ms"],
                "kp_counterpart_clusters": out["kp_counterpart_clusters"],
                "taker_locked_1c_candidates": out["taker_locked_1c_candidates"],
                "taker_locked_2c_candidates": out["taker_locked_2c_candidates"],
                "taker_locked_3c_candidates": out["taker_locked_3c_candidates"],
                "fetch_error_count": out["fetch_error_count"],
                "lifetime_measurement": out["lifetime_measurement"],
                "read_only": True,
                "order_entry": False,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _emit("SPORTS_KP_EDGE_CENSUS_ERROR", {
                "cycle": cycle,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "read_only": True,
                "order_entry": False,
            })
        await asyncio.sleep(max(0.0, INTERVAL_S - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(run_forever())
