"""Read-only Kalshi <-> Polymarket MLB full-game edge census.

Uses existing exact full-game normalization and fee-inclusive q=1 pricing. Kalshi
prices come from nested event snapshots, so broad incidence does not consume an
authenticated Kalshi WebSocket or per-market orderbook calls.

Physical game identity is fail-closed: matching semantic settlement keys are not
enough because the same teams/family recur on different dates. Venue-id YYYY-MM-DD
wins when present; otherwise the normalized scheduled start is converted to the MLB
Eastern calendar date. Cross-date/unresolved pairs are rejected before any census
count.

A binary native market is normalized into two semantic rows. Mirrored semantic
YES/NO representations of the same two native purchases are deduped into one
portfolio before incidence is reported. Existing full-game rule certificates are
then applied fail-closed; price-positive but conditional Kalshi/Polymarket rows are
kept visible but contribute zero to settlement-certified locked capacity.

This measures incidence/depth only. Repeated REST/event snapshots must not be used
to claim sub-second opportunity lifetimes. No credentials or order-entry code.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.mlb_game_rule_certifications import assess_lock
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
ET = ZoneInfo("America/New_York")
ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")


def _emit(stage: str, payload: dict[str, Any]) -> None:
    print(stage + " " + json.dumps(payload, separators=(",", ":"), default=str), flush=True)


def _physical_event_date(market_id: str, scheduled_start_ms: int) -> str:
    match = ISO_DATE_RE.search(str(market_id or ""))
    if match:
        return match.group(1)
    try:
        if int(scheduled_start_ms) <= 0:
            return ""
        dt = datetime.fromtimestamp(int(scheduled_start_ms) / 1000, tz=timezone.utc).astimezone(ET)
        return dt.date().isoformat()
    except Exception:
        return ""


def _quote_dates(normalized) -> dict[str, str]:
    out: dict[str, str] = {}
    for side in normalized:
        quote_id = f"{side.venue_market_id}#{side.side}"
        out[quote_id] = _physical_event_date(
            side.venue_market_id,
            int(side.identity.event.scheduled_start_ms or 0),
        )
    return out


def _dedupe_physical(rows):
    """One best row per logical settlement-key/direction after physical-date gate."""
    best = {}
    for row in rows:
        key = (row.settlement_key, row.yes_venue, row.no_venue)
        old = best.get(key)
        if old is None or row.unit_profit > old.unit_profit:
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.unit_profit, r.max_locked_profit), reverse=True)


def _market_and_source_side(quote_id: str) -> tuple[str, str]:
    base, sep, side = str(quote_id).rpartition("#")
    if not sep or side not in {"YES", "NO"}:
        return str(quote_id), "UNKNOWN"
    return base, side


def _native_purchase(venue: str, quote_id: str, semantic_side: str) -> tuple[str, str, str]:
    market_id, source_side = _market_and_source_side(quote_id)
    if source_side == "UNKNOWN":
        return str(venue), market_id, source_side
    if semantic_side == "YES":
        native_side = source_side
    else:
        native_side = "NO" if source_side == "YES" else "YES"
    return str(venue), market_id, native_side


def _actual_portfolio_key(row) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    legs = (
        _native_purchase(row.yes_venue, row.yes_market_id, "YES"),
        _native_purchase(row.no_venue, row.no_market_id, "NO"),
    )
    return tuple(sorted(legs))  # type: ignore[return-value]


def _dedupe_actual_portfolios(rows):
    best = {}
    for row in rows:
        key = _actual_portfolio_key(row)
        old = best.get(key)
        if old is None or row.unit_profit > old.unit_profit:
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.unit_profit, r.max_locked_profit), reverse=True)


def _rows_for_threshold(quotes, threshold: D, date_by_quote_id: dict[str, str]):
    raw = _dedupe(find_cross_venue_locks(
        [row.quote for row in quotes if row.quote.venue in {"kalshi", "polymarket"}],
        min_unit_profit=threshold,
        max_quote_skew_ms=5000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    kp = [r for r in raw if {r.yes_venue, r.no_venue} == {"kalshi", "polymarket"}]
    valid = []
    rejected = 0
    for row in kp:
        yes_date = date_by_quote_id.get(str(row.yes_market_id), "")
        no_date = date_by_quote_id.get(str(row.no_market_id), "")
        if not yes_date or not no_date or yes_date != no_date:
            rejected += 1
            continue
        valid.append(row)
    semantic = _dedupe_physical(valid)
    actual = _dedupe_actual_portfolios(semantic)
    certified = [row for row in actual if assess_lock(row).execution_eligible]
    return semantic, actual, certified, rejected, len(kp)


async def run_once() -> dict[str, Any]:
    started = time.monotonic()
    snapshot_ts_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly_raw, kalshi = await asyncio.gather(fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
        poly = full_game_only_events(poly_raw)
        pnorm = normalize_polymarket_mlb(poly)
        knorm = normalize_kalshi_mlb_eastern(kalshi)
        normalized = pnorm + knorm
        clusters = counterpart_clusters(normalized)
        priced, errors, key_count = await price_counterparts_snapshot(http, [], poly, kalshi)

    kp_quotes = [row for row in priced if row.quote.venue in {"kalshi", "polymarket"}]
    date_by_quote_id = _quote_dates(normalized)
    threshold_results = {str(t): _rows_for_threshold(kp_quotes, t, date_by_quote_id) for t in THRESHOLDS}
    semantic_by_threshold = {k: v[0] for k, v in threshold_results.items()}
    actual_by_threshold = {k: v[1] for k, v in threshold_results.items()}
    certified_by_threshold = {k: v[2] for k, v in threshold_results.items()}
    rejected_by_threshold = {k: v[3] for k, v in threshold_results.items()}
    raw_by_threshold = {k: v[4] for k, v in threshold_results.items()}

    top = []
    for row in actual_by_threshold["0.01"][:30]:
        assessment = assess_lock(row)
        item = row.as_dict()
        item["actual_portfolio_key"] = [list(leg) for leg in _actual_portfolio_key(row)]
        item["physical_game_date"] = date_by_quote_id.get(str(row.yes_market_id), "")
        item["passes_1c"] = row.unit_profit >= D("0.01")
        item["passes_2c"] = row.unit_profit >= D("0.02")
        item["passes_3c"] = row.unit_profit >= D("0.03")
        item["rule_assessment"] = assessment.as_dict()
        item["counts_toward_settlement_certified_capacity"] = assessment.execution_eligible
        item["visible_capacity_note"] = "q=1 top-of-book fee economics; final capacity still requires overlap and event-driven lifetime gates"
        top.append(item)

    kp_clusters = [r for r in clusters if set(r.get("venues") or []) == {"kalshi", "polymarket"}]
    out = {
        "status": "KALSHI_POLYMARKET_FULL_GAME_EDGE_CENSUS",
        "snapshot_ts_ms": snapshot_ts_ms,
        "scan_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "raw_events": {"polymarket": len(poly_raw), "kalshi": len(kalshi)},
        "normalized_sides": {"polymarket": len(pnorm), "kalshi": len(knorm)},
        "kp_semantic_clusters_pre_physical_gate": len(kp_clusters),
        "kp_clusters_by_family_pre_physical_gate": dict(Counter(r.get("market_family") for r in kp_clusters)),
        "normal_event_counterpart_keys_pre_physical_gate": key_count,
        "priced_kp_quotes": len(kp_quotes),
        "priced_kp_quotes_by_venue": dict(Counter(r.quote.venue for r in kp_quotes)),
        "raw_positive_candidates_pre_physical_gate": raw_by_threshold,
        "cross_date_or_unresolved_rejected": rejected_by_threshold,
        "semantic_positive_1c_rows": len(semantic_by_threshold["0.01"]),
        "semantic_positive_2c_rows": len(semantic_by_threshold["0.02"]),
        "semantic_positive_3c_rows": len(semantic_by_threshold["0.03"]),
        "pricing_positive_actual_portfolios_1c": len(actual_by_threshold["0.01"]),
        "pricing_positive_actual_portfolios_2c": len(actual_by_threshold["0.02"]),
        "pricing_positive_actual_portfolios_3c": len(actual_by_threshold["0.03"]),
        "settlement_certified_locked_1c": len(certified_by_threshold["0.01"]),
        "settlement_certified_locked_2c": len(certified_by_threshold["0.02"]),
        "settlement_certified_locked_3c": len(certified_by_threshold["0.03"]),
        "top_pricing_positive_actual_1c": top,
        "fetch_error_count": len(errors),
        "fetch_errors": errors[:20],
        "families": ["moneyline", "spread", "game_total"],
        "scope_gate": "POLYMARKET_MAIN_FULL_GAME_ONLY",
        "physical_event_gate": "EXACT_MLB_EASTERN_DATE_REQUIRED_MARKET_ID_DATE_PREFERRED",
        "portfolio_dedupe": "NATIVE_VENUE_MARKET_SIDE_PURCHASE_SET",
        "rule_gate": "MLB_GAME_RULE_CERTIFICATIONS_FAIL_CLOSED",
        "rule_audit_date": "2026-08-18",
        "capacity_note": "pricing-positive conditional portfolios are not locked arb; only settlement_certified_locked_* may enter final numerator, then depth overlap/latency/capital filters still apply",
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
                "cross_date_or_unresolved_rejected": out["cross_date_or_unresolved_rejected"],
                "pricing_positive_actual_portfolios_1c": out["pricing_positive_actual_portfolios_1c"],
                "pricing_positive_actual_portfolios_2c": out["pricing_positive_actual_portfolios_2c"],
                "pricing_positive_actual_portfolios_3c": out["pricing_positive_actual_portfolios_3c"],
                "settlement_certified_locked_1c": out["settlement_certified_locked_1c"],
                "settlement_certified_locked_2c": out["settlement_certified_locked_2c"],
                "settlement_certified_locked_3c": out["settlement_certified_locked_3c"],
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
