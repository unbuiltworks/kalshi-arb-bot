"""Read-only weather certification dimension audit.

The canonical weather row intentionally fails closed, but its aggregate blocker list can
mix settlement/rule provenance with quote, fee, and model readiness.  This helper keeps
those dimensions separate for portfolio/reporting use without changing any upstream
readiness state.

It never authorizes order entry or live probes.
"""
from __future__ import annotations

from typing import Any

SETTLEMENT_BLOCKERS = {
    "RULES_PENDING",
    "EXACT_MARKET_IDENTITY_UNVERIFIED",
    "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED",
    "EXACT_MARKET_SOURCE_AUDIT_REQUIRED",
    "EXACT_MARKET_EVIDENCE_MISMATCH",
    "EXACT_MARKET_EVIDENCE_UNOFFICIAL",
    "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT",
    "EXACT_MARKET_SETTLEMENT_SOURCE_UNVERIFIED",
    "STATION_IDENTITY_UNVERIFIED",
    "LOCAL_DAY_UNVERIFIED",
    "BRACKET_SEMANTICS_UNVERIFIED",
    "SETTLEMENT_SOURCE_UNVERIFIED",
}

MARKET_DATA_BLOCKERS = {
    "BOOK_UNVERIFIED",
    "QUOTE_STALE",
}

FEE_BLOCKERS = {
    "FEE_UNVERIFIED",
    "FEE_SCHEDULE_UNVERIFIED",
    "FEE_HISTORY_UNVERIFIED",
    "AUTHORITATIVE_FEE_AUDIT_REQUIRED",
    "EXACT_NET_FEE_ROUNDING_UNVERIFIED",
}

MODEL_BLOCKER_PREFIXES = (
    "FORECAST_",
    "MODEL_",
    "INDEPENDENT_ANCHOR_",
)
MODEL_BLOCKERS = {
    "LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY",
    "MODEL_SANITY_AUDIT_REQUIRED",
    "MODEL_RESEARCH_ONLY",
}


def _literal_true(value: Any) -> bool:
    return value is True


def _normalized_blockers(row: dict[str, Any]) -> set[str]:
    values = row.get("blockers") or row.get("readiness_blockers") or []
    if not isinstance(values, (list, tuple, set)):
        return {"MALFORMED_BLOCKER_TELEMETRY"}
    return {str(value).strip() for value in values if str(value).strip()}


def audit_weather_certification_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    """Split settlement, market-data, fee, and model evidence fail-closed.

    ``settlement_rules_provenance_verified`` is intentionally narrower than executable
    economics.  ``executable_shadow_prereqs_verified`` requires *all* four dimensions,
    explicit read-only state, and literal upstream certification flags where present.
    It is still research-only and can never set live readiness.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers = _normalized_blockers(row)
    settlement = sorted(blockers & SETTLEMENT_BLOCKERS)
    market_data = sorted(blockers & MARKET_DATA_BLOCKERS)
    fee = sorted(blockers & FEE_BLOCKERS)
    model = sorted(
        blocker for blocker in blockers
        if blocker in MODEL_BLOCKERS or blocker.startswith(MODEL_BLOCKER_PREFIXES)
    )
    classified = set(settlement) | set(market_data) | set(fee) | set(model)
    unclassified = sorted(blockers - classified)

    exact_source = row.get("exact_market_source_verified")
    if exact_source is False:
        settlement.append("EXACT_MARKET_SOURCE_AUDIT_REQUIRED")
    settlement = sorted(set(settlement))

    settlement_verified = not settlement and exact_source is not False
    market_data_verified = not market_data
    fee_verified = not fee
    model_verified = not model

    # Never upgrade malformed/unknown blocker telemetry into readiness.
    no_unknowns = not unclassified and "MALFORMED_BLOCKER_TELEMETRY" not in blockers
    upstream_ev = row.get("ev_certified_shadow")
    if upstream_ev is not None and not _literal_true(upstream_ev):
        upstream_ev_ok = False
    else:
        upstream_ev_ok = True

    executable_shadow = (
        settlement_verified
        and market_data_verified
        and fee_verified
        and model_verified
        and no_unknowns
        and upstream_ev_ok
        and row.get("order_entry") is False
        and row.get("live_probe_ready") is False
    )

    return {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_rules_provenance_verified": settlement_verified,
        "market_data_verified": market_data_verified,
        "fee_economics_verified": fee_verified,
        "model_probability_verified": model_verified,
        "executable_shadow_prereqs_verified": executable_shadow,
        "settlement_blockers": settlement,
        "market_data_blockers": market_data,
        "fee_blockers": fee,
        "model_blockers": model,
        "unclassified_blockers": unclassified,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
