"""Fail-closed weather portfolio metric binding to depth-proven OPEN outcomes.

V5 proves counted OPEN records are canonical-looking, exact-scope, time-bound evidence.
V6 additionally proves each record's ``execution_success`` value from displayed depth
rather than trusting that producer-supplied boolean. Because the v4/v5 cohort already
requires the exact market/side/quantity, ask ceiling, and minimum after-fee edge, the
remaining executable condition at each OPEN is that displayed consumable contracts are
at least the scored quantity.

Research-only. No order-entry or live-probe path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from src.weather_portfolio_metric_binding_guard_v5 import (
    audit_weather_portfolio_metric_binding_v5,
)

D = Decimal

EPISODE_DEPTH_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_DEPTH_UNVERIFIED"
EPISODE_SUCCESS_DEPTH_MISMATCH = "WEATHER_EXECUTION_EPISODE_SUCCESS_DEPTH_MISMATCH"


def _d(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def audit_weather_portfolio_metric_binding_v6(row: dict[str, Any]) -> dict[str, Any]:
    """Require record-level execution success to be independently proven by depth."""
    base = audit_weather_portfolio_metric_binding_v5(row)
    blockers = list(base.get("blockers") or [])

    records = row.get("executable_probability_episode_records")
    expected_qty = _d(row.get("executable_probability_quantity"))

    depth_verified = (
        expected_qty is not None
        and expected_qty > 0
        and isinstance(records, (list, tuple))
        and bool(records)
    )
    success_matches_depth = depth_verified

    if depth_verified:
        for record in records:
            if not isinstance(record, dict):
                depth_verified = False
                success_matches_depth = False
                break
            available = _d(record.get("available_contracts"))
            if available is None or available < 0:
                depth_verified = False
                success_matches_depth = False
                break
            observed_success = record.get("execution_success")
            if not isinstance(observed_success, bool):
                success_matches_depth = False
                continue
            derived_success = available >= expected_qty
            if observed_success is not derived_success:
                success_matches_depth = False

    if not depth_verified:
        blockers.append(EPISODE_DEPTH_UNVERIFIED)
    if depth_verified and not success_matches_depth:
        blockers.append(EPISODE_SUCCESS_DEPTH_MISMATCH)

    verified = (
        base.get("weather_portfolio_metric_binding_verified") is True
        and depth_verified
        and success_matches_depth
    )

    return {
        **base,
        "weather_portfolio_metric_binding_verified": verified,
        "weather_execution_episode_depth_verified": verified,
        "weather_execution_episode_success_depth_consistent": verified,
        "blockers": list(dict.fromkeys(blockers)),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
