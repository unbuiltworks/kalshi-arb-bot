"""Fail-closed quote freshness binding for weather SHADOW economics.

A producer-supplied ``quote_age_ms`` is not affirmative freshness evidence by itself.
This guard binds the reported age to timezone-aware quote and evaluation timestamps and
fails closed on malformed, future, or inconsistent telemetry. It is read-only and can
never authorize order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any


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


def _nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def audit_weather_quote_freshness_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Verify reported quote age against the exact quote/evaluation timestamps.

    ``quote_age_tolerance_ms`` exists only to absorb timestamp serialization/jitter and
    must itself be finite/nonnegative. The default is 5 ms. A future quote, missing
    evaluation timestamp, or age mismatch fails closed.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    quote_at = _aware_iso(row.get("quote_observed_at"))
    evaluated_at = _aware_iso(row.get("quote_age_evaluated_at"))
    reported_age = _nonnegative_decimal(row.get("quote_age_ms"))
    max_age = _nonnegative_decimal(row.get("max_quote_age_ms"))
    tolerance = _nonnegative_decimal(row.get("quote_age_tolerance_ms", 5))

    derived_age: Decimal | None = None
    if quote_at is None or evaluated_at is None:
        blockers.append("QUOTE_FRESHNESS_TIME_UNVERIFIED")
    else:
        delta_ms = Decimal(str((evaluated_at - quote_at).total_seconds())) * Decimal("1000")
        if delta_ms < 0:
            blockers.append("QUOTE_TIMESTAMP_IN_FUTURE")
        else:
            derived_age = delta_ms

    if reported_age is None or max_age is None or tolerance is None:
        blockers.append("QUOTE_FRESHNESS_NUMERIC_EVIDENCE_UNVERIFIED")
    elif reported_age > max_age:
        blockers.append("QUOTE_STALE")

    if derived_age is not None and reported_age is not None and tolerance is not None:
        if abs(derived_age - reported_age) > tolerance:
            blockers.append("QUOTE_AGE_TIMESTAMP_MISMATCH")
        if max_age is not None and derived_age > max_age:
            blockers.append("QUOTE_STALE")

    blockers = sorted(set(blockers))
    return {
        "category": "weather",
        "classification": "SHADOW",
        "quote_freshness_binding_verified": not blockers,
        "quote_freshness_binding_blockers": blockers,
        "derived_quote_age_ms": str(derived_age) if derived_age is not None else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
