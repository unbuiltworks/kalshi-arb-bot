"""Canonical read-only fixed-point weather economics entrypoint.

This v2 wrapper prevents caller-supplied exact fee numbers from bypassing quote
identity binding. Conservative public-data fee bounds remain available when exact
fee evidence is absent or fails validation.
"""
from __future__ import annotations

from typing import Any

from src.weather_exact_fee_context_guard import validate_weather_exact_fee_context
from src.weather_fixed_point_shadow import build_fixed_point_research_economics


def build_fixed_point_research_economics_v2(
    *,
    quote: dict[str, Any],
    fee_type: Any,
    fee_multiplier: Any,
    yes_probability: Any = None,
    exact_fee_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fee_audit = validate_weather_exact_fee_context(
        quote=quote,
        fee_context=exact_fee_context,
    )
    exact_verified = fee_audit["exact_fee_context_verified"] is True
    result = build_fixed_point_research_economics(
        quote=quote,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        yes_probability=yes_probability,
        exact_net_fee_dollars=(
            fee_audit["exact_net_fee_dollars"] if exact_verified else None
        ),
        exact_fee_context_verified=exact_verified,
    )

    blockers = sorted(set(result.get("blockers", [])) | set(fee_audit.get("blockers", [])))
    result["blockers"] = blockers
    result["exact_fee_binding_audit"] = fee_audit
    result["exact_fee_context_verified"] = exact_verified
    result["exact_after_fee_economics_verified"] = (
        result.get("exact_after_fee_economics_verified") is True and exact_verified
    )
    if not result["exact_after_fee_economics_verified"]:
        # Preserve conservative bounded economics, but never retain an exact-economics
        # status when the identity-bound exact-fee audit failed.
        if result.get("conservative_fee_bounded_economics_available") is True:
            result["economics_status"] = "CONSERVATIVE_FEE_BOUNDED_SHADOW_ECONOMICS"
        else:
            result["economics_status"] = "RESEARCH_ONLY_FEE_ECONOMICS_WITHHELD"
        for row in result.get("book_sides", []):
            row["exact_net_fee_dollars"] = None
            row["exact_after_fee_model_ev_dollars"] = None

    result["read_only"] = True
    result["order_entry"] = False
    result["live_probe_ready"] = False
    return result
