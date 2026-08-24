"""Read-only weather certification dimension audit.

The canonical weather row intentionally fails closed, but its aggregate blocker list can
mix settlement/rule provenance with quote, fee, and model readiness. This helper keeps
those dimensions separate for portfolio/reporting use without changing any upstream
readiness state.

A critical invariant here is that *absence of a blocker is not affirmative evidence*.
Each dimension requires explicit, literal verification evidence in addition to having no
classified blockers. It never authorizes order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.weather_fee_resolution_binding_guard import audit_weather_fee_resolution_binding
from src.weather_quote_freshness_binding_guard import audit_weather_quote_freshness_binding

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
    "TWC_SOURCE_ACCESS_REQUIRED",
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

_CANONICAL_FEE_SOURCES = {
    "series_current",
    "scheduled_fee_change",
    "historical_fee_change",
}


def _normalized_blockers(row: dict[str, Any]) -> set[str]:
    values = row.get("blockers") or row.get("readiness_blockers") or []
    if not isinstance(values, (list, tuple, set)):
        return {"MALFORMED_BLOCKER_TELEMETRY"}
    return {str(value).strip() for value in values if str(value).strip()}


def _finite_nonnegative(value: Any) -> bool:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return False
    return parsed.is_finite() and parsed >= 0


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def _aware_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _valid_executable_side(row: dict[str, Any], side: str) -> bool:
    """Accept current fixed-point books or legacy integer books without inventing depth.

    The certification dimension is intended to support a q1 micro-probe research view,
    so sub-contract displayed quantity (<1.00) is useful diagnostics but is not enough
    to clear executable-book evidence. Fixed-point price/quantity is preferred because
    current Kalshi books can use sub-cent prices and fractional quantities.
    """
    price_fp = _decimal(row.get(f"{side}_ask_dollars"))
    qty_fp = _decimal(row.get(f"{side}_ask_qty_fp"))
    if price_fp is not None and qty_fp is not None:
        return Decimal("0") < price_fp < Decimal("1") and qty_fp >= Decimal("1")

    cents = row.get(f"{side}_ask_cents")
    qty = row.get(f"{side}_available_contracts")
    return (
        isinstance(cents, int)
        and not isinstance(cents, bool)
        and 1 <= cents <= 99
        and isinstance(qty, int)
        and not isinstance(qty, bool)
        and qty >= 1
    )


def _book_evidence_verified(row: dict[str, Any]) -> bool:
    """Require timestamp-bound freshness plus an actually observed q1-executable book."""
    freshness = audit_weather_quote_freshness_binding(row)
    if freshness.get("quote_freshness_binding_verified") is not True:
        return False
    return any(_valid_executable_side(row, side) for side in ("yes", "no"))


def _fee_evidence_verified(row: dict[str, Any]) -> bool:
    """Require authoritative fee metadata plus exact quote-time resolver binding.

    A non-empty source label and ``fee_schedule_checked=True`` are not sufficient:
    otherwise a partial/empty fee-change response can silently make current Series
    metadata look authoritative even when a scheduled change was omitted. Certification
    therefore requires literal completeness evidence, exact Series binding, a
    timezone-aware fee-feed observation at or after the quote, *and* an independently
    verified quote-time fee-resolution record whose series/type/multiplier/source match
    the values being scored.
    """
    multiplier = row.get("fee_multiplier")
    series_ticker = str(row.get("series_ticker") or "").strip().upper()
    fee_series_ticker = str(row.get("fee_series_ticker") or "").strip().upper()
    fee_source = str(row.get("fee_metadata_source") or "").strip()
    quote_observed = _aware_iso(row.get("quote_observed_at"))
    fee_schedule_observed = _aware_iso(row.get("fee_schedule_observed_at"))

    basic_evidence = (
        row.get("fee_type") == "quadratic"
        and _finite_nonnegative(multiplier)
        and fee_source in _CANONICAL_FEE_SOURCES
        and row.get("fee_schedule_checked") is True
        and row.get("fee_schedule_feed_complete") is True
        and bool(series_ticker)
        and fee_series_ticker == series_ticker
        and quote_observed is not None
        and fee_schedule_observed is not None
        and fee_schedule_observed >= quote_observed
    )
    if not basic_evidence:
        return False

    binding = audit_weather_fee_resolution_binding(row)
    return binding.get("fee_resolution_binding_verified") is True


def _model_evidence_verified(row: dict[str, Any]) -> bool:
    """Require the canonical exact-market model audit chain to have passed."""
    probability = row.get("forecast_bracket_probability")
    try:
        p = Decimal(str(probability))
        probability_ok = p.is_finite() and Decimal(0) <= p <= Decimal(1)
    except Exception:
        probability_ok = False

    return (
        probability_ok
        and row.get("model_sanity_verified") is True
        and row.get("model_provider_provenance_verified") is True
        and row.get("local_day_binding_verified") is True
        and row.get("forecast_valid_local_day_verified") is True
        and row.get("anchor_valid_local_day_verified") is True
        and row.get("model_run_quote_time_verified") is True
    )


def audit_weather_certification_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    """Split settlement, market-data, fee, and model evidence fail-closed.

    ``settlement_rules_provenance_verified`` is intentionally narrower than executable
    economics. ``executable_shadow_prereqs_verified`` requires *all* four dimensions,
    explicit read-only state, and literal upstream certification flags. It is still
    research-only and can never set live readiness.
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
    if exact_source is not True:
        settlement.append("EXACT_MARKET_SOURCE_AUDIT_REQUIRED")
    if row.get("settlement_certified_shadow") is not True:
        settlement.append("SETTLEMENT_CERTIFICATION_EVIDENCE_REQUIRED")
    settlement = sorted(set(settlement))

    freshness_binding = audit_weather_quote_freshness_binding(row)
    if freshness_binding.get("quote_freshness_binding_verified") is not True:
        market_data.extend(freshness_binding.get("quote_freshness_binding_blockers") or [])
    if not _book_evidence_verified(row):
        market_data.append("EXECUTABLE_BOOK_EVIDENCE_REQUIRED")
    market_data = sorted(set(market_data))

    fee_binding = audit_weather_fee_resolution_binding(row)
    if fee_binding.get("fee_resolution_binding_verified") is not True:
        fee.extend(fee_binding.get("fee_resolution_binding_blockers") or [])
    if not _fee_evidence_verified(row):
        fee.append("AUTHORITATIVE_FEE_EVIDENCE_REQUIRED")
    fee = sorted(set(fee))

    if not _model_evidence_verified(row):
        model.append("MODEL_AUDIT_CHAIN_EVIDENCE_REQUIRED")
    model = sorted(set(model))

    settlement_verified = not settlement
    market_data_verified = not market_data
    fee_verified = not fee
    model_verified = not model

    # Never upgrade malformed/unknown blocker telemetry into readiness.
    no_unknowns = not unclassified and "MALFORMED_BLOCKER_TELEMETRY" not in blockers
    upstream_ev_ok = row.get("ev_certified_shadow") is True

    executable_shadow = (
        settlement_verified
        and market_data_verified
        and fee_verified
        and model_verified
        and no_unknowns
        and upstream_ev_ok
        and row.get("read_only") is True
        and row.get("order_entry") is False
        and row.get("live_probe_ready") is False
    )

    return {
        "category": "weather",
        "classification": "SHADOW",
        "settlement_rules_provenance_verified": settlement_verified,
        "market_data_verified": market_data_verified,
        "quote_freshness_binding_verified": freshness_binding.get("quote_freshness_binding_verified") is True,
        "derived_quote_age_ms": freshness_binding.get("derived_quote_age_ms"),
        "fee_economics_verified": fee_verified,
        "fee_resolution_binding_verified": fee_binding.get("fee_resolution_binding_verified") is True,
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
