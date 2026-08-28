"""Fail-closed binding audit for weather quote-time Kalshi fee resolution.

This module is read-only.  It exists because a reporting row must not become
fee-certified merely by carrying plausible-looking ``fee_type``, ``fee_multiplier``
and ``fee_metadata_source`` fields.  The scored fee must be explicitly bound to the
same Series and quote timestamp as the market observation and to a completed
quote-time fee-resolution pass.

It never authorizes order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

_CANONICAL_SOURCES = {
    "series_current",
    "scheduled_fee_change",
    "historical_fee_change",
}


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


def _finite_nonnegative(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def audit_weather_fee_resolution_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Verify that scored fee metadata is bound to this exact Series/quote.

    Required evidence is deliberately redundant with the fee resolver.  The central
    portfolio/certification layer is a trust boundary and therefore must not infer
    resolver success from an absence of blockers or from a source label alone.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    series = str(row.get("series_ticker") or "").strip().upper()
    resolved_series = str(row.get("fee_resolution_series_ticker") or "").strip().upper()
    source = str(row.get("fee_metadata_source") or "").strip()
    resolved_source = str(row.get("fee_resolution_source") or "").strip()
    quote_at = _aware_iso(row.get("quote_observed_at"))
    resolved_at = _aware_iso(row.get("fee_resolution_observed_at"))
    schedule_at = _aware_iso(row.get("fee_schedule_observed_at"))
    effective_at = _aware_iso(row.get("fee_resolution_effective_at"))
    multiplier = _finite_nonnegative(row.get("fee_multiplier"))
    resolved_multiplier = _finite_nonnegative(row.get("fee_resolution_fee_multiplier"))

    if row.get("fee_resolution_verified") is not True:
        blockers.append("FEE_RESOLUTION_NOT_VERIFIED")
    if not series or resolved_series != series:
        blockers.append("FEE_RESOLUTION_SERIES_MISMATCH")
    if row.get("fee_type") != "quadratic" or row.get("fee_resolution_fee_type") != "quadratic":
        blockers.append("FEE_RESOLUTION_TYPE_MISMATCH")
    if source not in _CANONICAL_SOURCES or resolved_source != source:
        blockers.append("FEE_RESOLUTION_SOURCE_MISMATCH")
    if multiplier is None or resolved_multiplier is None or multiplier != resolved_multiplier:
        blockers.append("FEE_RESOLUTION_MULTIPLIER_MISMATCH")
    if row.get("fee_schedule_feed_complete") is not True:
        blockers.append("FEE_SCHEDULE_COMPLETENESS_UNVERIFIED")
    if quote_at is None or resolved_at is None or schedule_at is None or effective_at is None:
        blockers.append("FEE_RESOLUTION_TIME_UNVERIFIED")
    else:
        if resolved_at < quote_at or schedule_at < quote_at:
            blockers.append("FEE_RESOLUTION_OBSERVED_BEFORE_QUOTE")
        if effective_at > quote_at:
            blockers.append("FEE_RESOLUTION_FUTURE_EFFECTIVE_RECORD")

    blockers = sorted(set(blockers))
    return {
        "category": "weather",
        "classification": "SHADOW",
        "fee_resolution_binding_verified": not blockers,
        "fee_resolution_binding_blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
