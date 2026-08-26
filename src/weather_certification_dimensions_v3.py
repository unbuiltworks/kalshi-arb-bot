"""Canonical weather certification wrapper with independently recomputed selected-side economics.

The base certification report verifies that the producer-declared scored side is bound to
an executable q1 book and matching side economics. That is necessary but not sufficient:
a stale producer could still declare the lower-EV side and provide internally consistent
top-level fields for that side, or attach fabricated probability/fee/EV arithmetic to a
plausible current book.

This read-only wrapper first runs ``normalize_selected_side_schema`` which requires
exactly one YES and one NO economics record, independently recomputes the unique best
side from modeled after-fee EV, and fails closed on ties, malformed economics, or a
producer-declared side that disagrees with the recomputation. It then independently
recomputes total after-fee EV for both sides and verifies that edge-per-contract equals
that total EV divided by the scored quantity for both sides. Only rows that pass every
check can retain executable-shadow prerequisite status from the central certification
dimension audit.

It never grants live readiness or order entry.
"""
from __future__ import annotations

from typing import Any

from src.weather_certification_dimensions import audit_weather_certification_dimensions
from src.weather_selected_side_edge_guard import audit_weather_selected_side_edge
from src.weather_selected_side_economics_arithmetic_guard import (
    audit_weather_selected_side_economics_arithmetic,
)
from src.weather_selected_side_schema_guard import normalize_selected_side_schema


def audit_weather_certification_dimensions_v3(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    normalized = normalize_selected_side_schema(row)
    selected_verified = normalized.get("selected_side_schema_verified") is True

    if selected_verified:
        arithmetic = audit_weather_selected_side_economics_arithmetic(normalized)
        edge_audit = audit_weather_selected_side_edge(normalized)
    else:
        arithmetic = {
            "selected_side_economics_arithmetic_verified": False,
            "recomputed_after_fee_ev_cents": None,
            "reported_after_fee_ev_cents": None,
            "blockers": ["SELECTED_SIDE_SCHEMA_UNVERIFIED"],
        }
        edge_audit = {
            "selected_side_edge_per_contract_verified": False,
            "recomputed_side_edge_cents_per_contract": {},
            "blockers": ["SELECTED_SIDE_SCHEMA_UNVERIFIED"],
        }
    arithmetic_verified = arithmetic.get("selected_side_economics_arithmetic_verified") is True
    edge_verified = edge_audit.get("selected_side_edge_per_contract_verified") is True

    out = audit_weather_certification_dimensions(normalized)
    selected_blockers = list(normalized.get("selected_side_schema_blockers") or [])
    arithmetic_blockers = list(arithmetic.get("blockers") or [])
    edge_blockers = list(edge_audit.get("blockers") or [])

    if not selected_verified or not arithmetic_verified or not edge_verified:
        market_blockers = set(out.get("market_data_blockers") or [])
        if not selected_verified:
            market_blockers.update(selected_blockers or ["SELECTED_SIDE_SCHEMA_UNVERIFIED"])
        if not arithmetic_verified:
            market_blockers.update(
                arithmetic_blockers or ["SELECTED_SIDE_ECONOMICS_ARITHMETIC_UNVERIFIED"]
            )
        if not edge_verified:
            market_blockers.update(
                edge_blockers or ["SELECTED_SIDE_EDGE_PER_CONTRACT_UNVERIFIED"]
            )
        out["market_data_blockers"] = sorted(market_blockers)
        out["market_data_verified"] = False
        out["executable_shadow_prereqs_verified"] = False

    out["selected_side_schema_verified"] = selected_verified
    out["selected_side_schema_blockers"] = selected_blockers
    out["selected_side_recomputed"] = normalized.get("selected_side_recomputed") is True
    out["selected_side_economics_arithmetic_verified"] = arithmetic_verified
    out["selected_side_economics_arithmetic_blockers"] = arithmetic_blockers
    out["selected_side_recomputed_after_fee_ev_cents"] = arithmetic.get(
        "recomputed_after_fee_ev_cents"
    )
    out["selected_side_edge_per_contract_verified"] = edge_verified
    out["selected_side_edge_per_contract_blockers"] = edge_blockers
    out["recomputed_side_edge_cents_per_contract"] = edge_audit.get(
        "recomputed_side_edge_cents_per_contract", {}
    )
    out["best_shadow_side"] = normalized.get("best_shadow_side")
    out["modeled_after_fee_ev_cents"] = normalized.get("modeled_after_fee_ev_cents")
    out["modeled_after_fee_edge_cents_per_contract"] = normalized.get(
        "modeled_after_fee_edge_cents_per_contract"
    )
    out["read_only"] = True
    out["order_entry"] = False
    out["live_probe_ready"] = False
    return out
