"""Continuous read-only weather shadow scanner for Railway.

No authentication or order-entry imports are allowed here. The daemon polls public
Kalshi weather markets, emits normalized shadow rows, and depth-checks only the
strongest positive research candidates using Kalshi's public order-book endpoint.

Depth-backed research is deliberately kept separate from executable-shadow
readiness. A real order book can be measured while settlement/model certification
is still unresolved; that evidence must never be labeled execution-eligible.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone

from src.weather_orderbook import executable_grid, fetch_orderbook
from src.weather_shadow import WEATHER_SERIES, WeatherShadowRow, scan_weather


def _candidate_score(row: WeatherShadowRow) -> float:
    if row.model_eligibility != "FORECAST_ONLY_RESEARCH":
        return 0.0
    vals = [v for v in (row.q1_yes_model_ev_cents, row.q1_no_model_ev_cents) if v is not None]
    return max(vals, default=0.0)


def _candidate_sides(row: WeatherShadowRow, min_ev_cents: float) -> list[tuple[str, float]]:
    if row.model_eligibility != "FORECAST_ONLY_RESEARCH":
        return []
    out: list[tuple[str, float]] = []
    if row.q1_yes_model_ev_cents is not None and row.q1_yes_model_ev_cents >= min_ev_cents:
        out.append(("yes", float(row.q1_yes_model_ev_cents)))
    if row.q1_no_model_ev_cents is not None and row.q1_no_model_ev_cents >= min_ev_cents:
        out.append(("no", float(row.q1_no_model_ev_cents)))
    return out


def _research_blockers(row: WeatherShadowRow) -> list[str]:
    """Return fail-closed blockers for anything stronger than depth research.

    The active weather integration branch does not yet carry the universal lane's
    exact-market source audit or authoritative Series/exact-fee certification on
    every normalized row.  Do not let future settlement/model recovery silently
    turn that incomplete telemetry into execution-eligible economics.
    """
    blockers: list[str] = []
    if row.settlement_status != "SETTLEMENT_CERTIFIED":
        blockers.append(row.settlement_status or "SETTLEMENT_UNVERIFIED")
    if row.model_status != "MODEL_CERTIFIED":
        blockers.append(row.model_status or "MODEL_UNVERIFIED")
    if row.data_quality_status != "FRESH":
        blockers.append(f"DATA_QUALITY_{row.data_quality_status or 'UNKNOWN'}")
    if getattr(row, "exact_market_source_audited", False) is not True:
        blockers.append("EXACT_MARKET_SOURCE_AUDIT_REQUIRED")
    if getattr(row, "authoritative_fee_audited", False) is not True:
        blockers.append("AUTHORITATIVE_FEE_AUDIT_REQUIRED")
    return blockers


def _fill_payload(fill) -> dict[str, object]:
    return {
        "requested_contracts": fill.requested_contracts,
        "filled_contracts": fill.filled_contracts,
        "fully_fillable": fill.fully_fillable,
        "total_cost_cents": fill.total_cost_cents,
        "average_price_cents": fill.average_price_cents,
        "worst_price_cents": fill.worst_price_cents,
        "levels_used": fill.levels_used,
    }


def _side_readiness_blockers(row: WeatherShadowRow, q1_fill) -> list[str]:
    blockers = list(_research_blockers(row))
    if q1_fill is None or q1_fill.fully_fillable is not True:
        blockers.append("Q1_DEPTH_NOT_FULLY_FILLABLE")
    elif q1_fill.filled_contracts < 1.0:
        blockers.append("Q1_DEPTH_UNDER_ONE_CONTRACT")
    return blockers


def run_forever() -> None:
    interval_s = max(30, int(os.getenv("WEATHER_SHADOW_INTERVAL_S", "60")))
    series_raw = os.getenv("WEATHER_SHADOW_SERIES", "").strip()
    series = [s.strip() for s in series_raw.split(",") if s.strip()] or list(WEATHER_SERIES)
    depth_candidates = max(1, min(25, int(os.getenv("WEATHER_DEPTH_CANDIDATES", "12"))))
    min_ev_cents = max(0.0, float(os.getenv("WEATHER_DEPTH_MIN_EV_CENTS", "1.0")))

    print(json.dumps({
        "event": "WEATHER_SHADOW_START",
        "classification": "SHADOW",
        "order_entry": False,
        "auth": False,
        "series": series,
        "interval_s": interval_s,
        "depth_candidates": depth_candidates,
        "depth_min_ev_cents": min_ev_cents,
        "ts": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True), flush=True)

    while True:
        started = time.monotonic()
        try:
            rows = scan_weather(series)
            for row in rows:
                print(json.dumps({"event": "WEATHER_SHADOW_ROW", **asdict(row)}, sort_keys=True), flush=True)

            candidates = [row for row in rows if _candidate_score(row) >= min_ev_cents]
            candidates.sort(key=_candidate_score, reverse=True)
            candidates = candidates[:depth_candidates]
            depth_checked = 0
            depth_research_events = 0
            execution_eligible_events = 0

            for row in candidates:
                sides = _candidate_sides(row, min_ev_cents)
                if not sides:
                    continue
                try:
                    book = fetch_orderbook(row.market_ticker, depth=100)
                    grid = executable_grid(book, max_q=5)
                    depth_checked += 1
                    for side, research_ev in sides:
                        side_grid = grid[side]
                        fills = {str(q): _fill_payload(side_grid[q]) for q in range(1, 6)}
                        blockers = _side_readiness_blockers(row, side_grid.get(1))
                        execution_eligible = not blockers
                        print(json.dumps({
                            "event": "WEATHER_DEPTH_RESEARCH_SHADOW",
                            "classification": "SHADOW",
                            "order_entry": False,
                            "execution_eligible": execution_eligible,
                            "readiness_blockers": blockers,
                            "settlement_status": row.settlement_status,
                            "settlement_source": row.settlement_source,
                            "settlement_location_key": row.settlement_location_key,
                            "model_status": row.model_status,
                            "model_eligibility": row.model_eligibility,
                            "data_quality_status": row.data_quality_status,
                            "market_ticker": row.market_ticker,
                            "event_ticker": row.event_ticker,
                            "city": row.city,
                            "title": row.title,
                            "side": side.upper(),
                            "q1_research_model_ev_cents": research_ev,
                            "model_yes_probability": row.model_yes_probability,
                            "displayed_yes_ask_cents": row.yes_ask_cents,
                            "displayed_no_ask_cents": row.no_ask_cents,
                            "fills_q1_to_q5": fills,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }, sort_keys=True), flush=True)
                        depth_research_events += 1
                        if execution_eligible:
                            execution_eligible_events += 1
                except Exception as exc:
                    print(json.dumps({
                        "event": "WEATHER_ORDERBOOK_ERROR",
                        "market_ticker": row.market_ticker,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                        "order_entry": False,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }, sort_keys=True), flush=True)

            settlement_states = sorted({row.settlement_status for row in rows})
            print(json.dumps({
                "event": "WEATHER_SHADOW_SUMMARY",
                "rows": len(rows),
                "positive_research_candidates": len(candidates),
                "depth_checked": depth_checked,
                "depth_research_events": depth_research_events,
                "execution_eligible_events": execution_eligible_events,
                "settlement_statuses": settlement_states,
                "classification": "SHADOW",
                "order_entry": False,
                "ts": datetime.now(timezone.utc).isoformat(),
            }, sort_keys=True), flush=True)
        except Exception as exc:  # keep observing; never mutate anything on error
            print(json.dumps({
                "event": "WEATHER_SHADOW_ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "order_entry": False,
                "ts": datetime.now(timezone.utc).isoformat(),
            }, sort_keys=True), flush=True)

        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval_s - elapsed))


if __name__ == "__main__":
    run_forever()
