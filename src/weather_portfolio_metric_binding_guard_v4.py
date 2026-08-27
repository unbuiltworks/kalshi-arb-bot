"""Fail-closed weather portfolio metric binding to explicit OPEN episode records.

V3 reconciles counts to unique episode IDs. IDs alone are still producer assertions:
a caller could invent labels without proving they came from the canonical weather
OPEN/MOVE/CLOSE tracker. V4 requires each counted ID to reconcile to an explicit OPEN
record for the exact market/side/quantity/price/min-edge cohort being ranked.

Research-only. No order-entry or live-probe path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from src.weather_portfolio_metric_binding_guard_v3 import (
    audit_weather_portfolio_metric_binding_v3,
)

D = Decimal

EPISODE_RECORDS_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_RECORDS_UNVERIFIED"
EPISODE_RECORD_SCOPE_MISMATCH = "WEATHER_EXECUTION_EPISODE_RECORD_SCOPE_MISMATCH"


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _norm_side(value: Any) -> str:
    return str(value or "").strip().upper()


def audit_weather_portfolio_metric_binding_v4(row: dict[str, Any]) -> dict[str, Any]:
    """Require counted execution episodes to be explicit canonical OPEN records."""
    base = audit_weather_portfolio_metric_binding_v3(row)
    blockers = list(base.get("blockers") or [])

    records = row.get("executable_probability_episode_records")
    all_ids = base.get("verified_executable_probability_episode_ids")
    success_ids = base.get("verified_executable_probability_success_episode_ids")

    expected_market = str(row.get("market_ticker") or "").strip().upper()
    expected_side = _norm_side(row.get("best_shadow_side"))
    expected_qty = _d(row.get("selected_quantity") or row.get("quantity"))
    expected_max_ask = _d(row.get("selected_ask_cents") or row.get("ask_cents"))
    expected_min_edge = _d(
        row.get("executable_probability_min_after_fee_edge_cents_per_contract")
    )

    parsed: dict[str, dict[str, Any]] = {}
    structurally_valid = isinstance(records, (list, tuple)) and bool(records)
    if structurally_valid:
        for record in records:
            if not isinstance(record, dict):
                structurally_valid = False
                break
            episode_id = str(record.get("episode_id") or "").strip()
            if not episode_id or episode_id in parsed:
                structurally_valid = False
                break
            if record.get("event") != "OPEN":
                structurally_valid = False
                break
            if record.get("category") != "weather" or record.get("read_only") is not True:
                structurally_valid = False
                break
            if record.get("order_entry") is not False or record.get("live_probe_ready") is not False:
                structurally_valid = False
                break
            parsed[episode_id] = record

    if not structurally_valid:
        blockers.append(EPISODE_RECORDS_UNVERIFIED)

    ids_reconcile = (
        structurally_valid
        and isinstance(all_ids, list)
        and set(parsed) == set(all_ids)
        and isinstance(success_ids, list)
        and set(success_ids).issubset(set(parsed))
    )
    if not ids_reconcile:
        blockers.append(EPISODE_RECORDS_UNVERIFIED)

    scope_verified = ids_reconcile and all(
        str(rec.get("market_ticker") or "").strip().upper() == expected_market
        and _norm_side(rec.get("side")) == expected_side
        and _d(rec.get("quantity")) == expected_qty
        and _d(rec.get("ask_cents")) is not None
        and expected_max_ask is not None
        and _d(rec.get("ask_cents")) <= expected_max_ask
        and _d(rec.get("modeled_after_fee_edge_cents_per_contract")) is not None
        and expected_min_edge is not None
        and _d(rec.get("modeled_after_fee_edge_cents_per_contract")) >= expected_min_edge
        for rec in parsed.values()
    )
    if not scope_verified:
        blockers.append(EPISODE_RECORD_SCOPE_MISMATCH)

    success_verified = scope_verified and all(
        rec_id in set(success_ids or [])
        for rec_id, rec in parsed.items()
        if rec.get("execution_success") is True
    ) and all(
        parsed[rec_id].get("execution_success") is True
        for rec_id in (success_ids or [])
    )
    if not success_verified:
        blockers.append(EPISODE_RECORDS_UNVERIFIED)

    verified = (
        base.get("weather_portfolio_metric_binding_verified") is True
        and scope_verified
        and success_verified
    )

    return {
        **base,
        "weather_portfolio_metric_binding_verified": verified,
        "weather_execution_episode_records_verified": verified,
        "verified_executable_probability_episode_record_count": len(parsed) if verified else 0,
        "blockers": list(dict.fromkeys(blockers)),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
