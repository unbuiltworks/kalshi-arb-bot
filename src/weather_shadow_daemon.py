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

from src.fee_calc import taker_fee_cents
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
    full exact-market source/fee/model certification payload on every normalized
    row. Do not let future settlement/model recovery silently turn incomplete
    telemetry into execution-eligible economics.
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
    # Model certification is not sufficient by itself. A large apparent bracket EV
    # must also have independently passed the same-station/local-day/calibration
    # sanity audit used by the universal read-only lane. Missing telemetry fails
    # closed; strings such as "true" are deliberately rejected.
    if getattr(row, "model_sanity_verified", False) is not True:
        blockers.append("MODEL_SANITY_AUDIT_REQUIRED")
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


def _depth_repriced_q1_ev_cents(row: WeatherShadowRow, side: str, q1_fill) -> tuple[float | None, int | None]:
    """Recompute q1 research EV from the actually consumable depth price.

    The top-level market snapshot and the deeper order-book fetch are not atomic. A
    displayed ask used by ``WeatherShadowRow`` can therefore differ from the price
    actually consumable when depth is fetched moments later. Once depth exists, the
    q1 research EV must use that q1 fill cost rather than the earlier displayed ask.

    This remains research-only: the fee here is the existing modeled Kalshi taker
    fee, not the authoritative fee audit required for executable-shadow readiness.
    """
    if (
        q1_fill is None
        or q1_fill.fully_fillable is not True
        or q1_fill.filled_contracts < 1.0
        or row.model_yes_probability is None
    ):
        return None, None
    try:
        cost_cents = float(q1_fill.total_cost_cents)
        p_yes = float(row.model_yes_probability)
    except (TypeError, ValueError):
        return None, None
    if not (0.0 <= p_yes <= 1.0) or not (0.0 < cost_cents < 100.0):
        return None, None

    side_norm = str(side).strip().lower()
    if side_norm not in {"yes", "no"}:
        return None, None
    # q1 total_cost is the executable book cost in cents. Fee calculation accepts
    # cents and remains explicitly modeled/research-only on this legacy integration
    # branch.
    fee_cents = taker_fee_cents(1, int(round(cost_cents)), row.market_ticker)
    payout_probability = p_yes if side_norm == "yes" else (1.0 - p_yes)
    ev_cents = round(payout_probability * 100.0 - cost_cents - fee_cents, 2)
    return ev_cents, fee_cents


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
            depth_repriced_positive_events = 0
            execution_eligible_events = 0

            for row in candidates:
                sides = _candidate_sides(row, min_ev_cents)
                if not sides:
                    continue
                try:
                    book = fetch_orderbook(row.market_ticker, depth=100)
                    grid = executable_grid(book, max_q=5)
                    depth_checked += 1
                    for side, predepth_research_ev in sides:
                        side_grid = grid[side]
                        q1_fill = side_grid.get(1)
                        fills = {str(q): _fill_payload(side_grid[q]) for q in range(1, 6)}
                        depth_repriced_ev, depth_repriced_fee = _depth_repriced_q1_ev_cents(row, side, q1_fill)
                        blockers = _side_readiness_blockers(row, q1_fill)
                        execution_eligible = not blockers
                        depth_repriced_positive = (
                            depth_repriced_ev is not None and depth_repriced_ev >= min_ev_cents
                        )
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
                            "model_sanity_verified": getattr(row, "model_sanity_verified", False) is True,
                            "data_quality_status": row.data_quality_status,
                            "market_ticker": row.market_ticker,
                            "event_ticker": row.event_ticker,
                            "city": row.city,
                            "title": row.title,
                            "side": side.upper(),
                            "predepth_research_model_ev_cents": predepth_research_ev,
                            "q1_research_model_ev_cents": depth_repriced_ev,
                            "q1_depth_repriced_modeled_fee_cents": depth_repriced_fee,
                            "q1_depth_repriced_positive": depth_repriced_positive,
                            "model_yes_probability": row.model_yes_probability,
                            "displayed_yes_ask_cents": row.yes_ask_cents,
                            "displayed_no_ask_cents": row.no_ask_cents,
                            "fills_q1_to_q5": fills,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }, sort_keys=True), flush=True)
                        depth_research_events += 1
                        if depth_repriced_positive:
                            depth_repriced_positive_events += 1
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
                "depth_repriced_positive_events": depth_repriced_positive_events,
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
