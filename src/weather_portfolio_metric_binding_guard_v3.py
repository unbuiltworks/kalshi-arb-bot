"""Fail-closed weather portfolio metric binding to explicit unique episodes.

The v2 guard requires execution probability to come from unique OPEN episode counts.
Counts alone are not auditable: a producer can accidentally duplicate, omit, or
misclassify episodes while still emitting internally consistent integers. This wrapper
requires those counts to reconcile to explicit deduplicated episode identifiers and
requires the successful set to be a subset of the exact scoped episode population.

This module is research-only and cannot authorize order entry or a live probe.
"""
from __future__ import annotations

from typing import Any

from src.weather_portfolio_metric_binding_guard_v2 import (
    audit_weather_portfolio_metric_binding_v2,
)

EPISODE_IDENTITY_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_IDENTITY_UNVERIFIED"
EPISODE_SET_RECONCILIATION_UNVERIFIED = (
    "WEATHER_EXECUTION_EPISODE_SET_RECONCILIATION_UNVERIFIED"
)
EPISODE_IDENTITY_SCOPE = "EXACT_MARKET_SAME_SIDE_QTY_PRICE_MIN_AFTER_FEE_EDGE"


def _normalized_ids(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    ids: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            return None
        episode_id = raw.strip()
        if episode_id in ids:
            return None
        ids.append(episode_id)
    return ids


def audit_weather_portfolio_metric_binding_v3(row: dict[str, Any]) -> dict[str, Any]:
    """Require unique-episode counts to reconcile to explicit episode identities."""
    base = audit_weather_portfolio_metric_binding_v2(row)
    blockers = list(base.get("blockers") or [])

    all_episode_ids = _normalized_ids(row.get("executable_probability_episode_ids"))
    success_episode_ids = _normalized_ids(
        row.get("executable_probability_success_episode_ids")
    )

    identity_verified = (
        row.get("executable_probability_episode_identity_verified") is True
        and row.get("executable_probability_episode_identity_scope")
        == EPISODE_IDENTITY_SCOPE
        and all_episode_ids is not None
        and success_episode_ids is not None
        and len(all_episode_ids) > 0
    )
    if not identity_verified:
        blockers.append(EPISODE_IDENTITY_UNVERIFIED)

    success_set = set(success_episode_ids or [])
    all_set = set(all_episode_ids or [])
    set_reconciled = (
        identity_verified
        and success_set.issubset(all_set)
        and len(all_episode_ids or [])
        == base.get("executable_probability_unique_episode_count")
        and len(success_episode_ids or [])
        == base.get("executable_probability_success_episode_count")
    )
    if not set_reconciled:
        blockers.append(EPISODE_SET_RECONCILIATION_UNVERIFIED)

    verified = (
        base.get("weather_portfolio_metric_binding_verified") is True
        and set_reconciled
    )

    return {
        **base,
        "weather_portfolio_metric_binding_verified": verified,
        "weather_execution_episode_identity_verified": identity_verified,
        "weather_execution_episode_set_reconciled": set_reconciled,
        "verified_executable_probability_episode_ids": (
            all_episode_ids if set_reconciled else None
        ),
        "verified_executable_probability_success_episode_ids": (
            success_episode_ids if set_reconciled else None
        ),
        "blockers": list(dict.fromkeys(blockers)),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
