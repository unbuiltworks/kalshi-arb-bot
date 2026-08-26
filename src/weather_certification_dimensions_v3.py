"""Canonical weather certification wrapper with independently recomputed selected-side EV.

The base certification report verifies that the producer-declared scored side is bound to
an executable q1 book and matching side economics. That is necessary but not sufficient:
a stale producer could still declare the lower-EV side and provide internally consistent
top-level fields for that side.

This read-only wrapper first runs ``normalize_selected_side_schema`` which requires
exactly one YES and one NO economics record, independently recomputes the unique best
side from modeled after-fee EV, and fails closed on ties, malformed economics, or a
producer-declared side that disagrees with the recomputation. Only the normalized row is
then passed into the central certification-dimension audit.

It never grants live readiness or order entry.
"""
from __future__ import annotations

from typing import Any

from src.weather_certification_dimensions import audit_weather_certification_dimensions
from src.weather_selected_side_schema_guard import normalize_selected_side_schema


def audit_weather_certification_dimensions_v3(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    normalized = normalize_selected_side_schema(row)
    selected_verified = normalized.get("selected_side_schema_verified") is True

    out = audit_weather_certification_dimensions(normalized)
    selected_blockers = list(normalized.get("selected_side_schema_blockers") or [])

    if not selected_verified:
        market_blockers = set(out.get("market_data_blockers") or [])
        market_blockers.update(selected_blockers or ["SELECTED_SIDE_SCHEMA_UNVERIFIED"])
        out["market_data_blockers"] = sorted(market_blockers)
        out["market_data_verified"] = False
        out["executable_shadow_prereqs_verified"] = False

    out["selected_side_schema_verified"] = selected_verified
    out["selected_side_schema_blockers"] = selected_blockers
    out["selected_side_recomputed"] = normalized.get("selected_side_recomputed") is True
    out["best_shadow_side"] = normalized.get("best_shadow_side")
    out["modeled_after_fee_ev_cents"] = normalized.get("modeled_after_fee_ev_cents")
    out["read_only"] = True
    out["order_entry"] = False
    out["live_probe_ready"] = False
    return out
