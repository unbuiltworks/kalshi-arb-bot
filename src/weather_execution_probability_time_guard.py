"""Fail-closed anti-lookahead and recency guard for weather execution-probability cohorts.

Portfolio execution probability must be estimated only from observations that existed
at or before the quote being ranked. A historical cohort that includes later snapshots
would leak future market-liquidity information into the current SHADOW score even if
market/side/quantity/price/edge scope is otherwise exact.

Avoiding lookahead is not enough: an arbitrarily old cohort can also misrepresent current
liquidity. Therefore the producer must supply an explicitly audited maximum cohort age,
and the cohort window end must be no older than that bound relative to the scored quote.
The cohort span itself must also be bounded: a recent window end cannot make months of
stale history representative of current liquidity. The guard does not invent either a
recency threshold or a maximum cohort span.

This helper is read-only and cannot authorize order entry or a live probe.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

EXECUTION_SAMPLE_TIME_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED"
EXECUTION_SAMPLE_LOOKAHEAD_DETECTED = "WEATHER_EXECUTION_SAMPLE_LOOKAHEAD_DETECTED"
EXECUTION_SAMPLE_RECENCY_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_RECENCY_UNVERIFIED"
EXECUTION_SAMPLE_STALE = "WEATHER_EXECUTION_SAMPLE_STALE"
EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED"
EXECUTION_SAMPLE_WINDOW_TOO_BROAD = "WEATHER_EXECUTION_SAMPLE_WINDOW_TOO_BROAD"


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    return out if out.tzinfo is not None and out.utcoffset() is not None else None


def _positive_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not out.is_finite() or out <= 0:
        return None
    return out


def audit_weather_execution_probability_time_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Bind an empirical execution cohort to timely information available by quote time."""
    quote = _dt(row.get("quote_observed_at"))
    start = _dt(row.get("executable_probability_window_start"))
    end = _dt(row.get("executable_probability_window_end"))

    blockers: list[str] = []
    structurally_verified = (
        row.get("executable_probability_time_scope_verified") is True
        and quote is not None
        and start is not None
        and end is not None
        and start <= end
    )
    if not structurally_verified:
        blockers.append(EXECUTION_SAMPLE_TIME_UNVERIFIED)

    lookahead = False
    age_seconds: Decimal | None = None
    window_span_seconds: Decimal | None = None
    if structurally_verified:
        age_seconds = Decimal(str((quote - end).total_seconds()))
        window_span_seconds = Decimal(str((end - start).total_seconds()))
        if end > quote:
            lookahead = True
            blockers.append(EXECUTION_SAMPLE_LOOKAHEAD_DETECTED)

    max_age_seconds = _positive_decimal(row.get("executable_probability_max_age_seconds"))
    recency_evidence_verified = (
        row.get("executable_probability_recency_verified") is True
        and max_age_seconds is not None
    )
    if not recency_evidence_verified:
        blockers.append(EXECUTION_SAMPLE_RECENCY_UNVERIFIED)

    stale = False
    if (
        structurally_verified
        and not lookahead
        and recency_evidence_verified
        and age_seconds is not None
        and age_seconds > max_age_seconds
    ):
        stale = True
        blockers.append(EXECUTION_SAMPLE_STALE)

    max_window_seconds = _positive_decimal(row.get("executable_probability_max_window_seconds"))
    window_span_evidence_verified = (
        row.get("executable_probability_window_span_verified") is True
        and max_window_seconds is not None
    )
    if not window_span_evidence_verified:
        blockers.append(EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED)

    window_too_broad = False
    if (
        structurally_verified
        and window_span_evidence_verified
        and window_span_seconds is not None
        and window_span_seconds > max_window_seconds
    ):
        window_too_broad = True
        blockers.append(EXECUTION_SAMPLE_WINDOW_TOO_BROAD)

    verified = (
        structurally_verified
        and not lookahead
        and recency_evidence_verified
        and not stale
        and window_span_evidence_verified
        and not window_too_broad
    )
    return {
        "weather_execution_probability_time_verified": verified,
        "weather_execution_probability_recency_verified": recency_evidence_verified and not stale,
        "weather_execution_probability_window_span_verified": (
            window_span_evidence_verified and not window_too_broad
        ),
        "quote_observed_at": None if quote is None else quote.isoformat(),
        "execution_probability_window_start": None if start is None else start.isoformat(),
        "execution_probability_window_end": None if end is None else end.isoformat(),
        "execution_probability_lookahead_detected": lookahead,
        "execution_probability_stale_detected": stale,
        "execution_probability_window_too_broad_detected": window_too_broad,
        "execution_probability_age_seconds": None if age_seconds is None else str(age_seconds),
        "execution_probability_max_age_seconds": None if max_age_seconds is None else str(max_age_seconds),
        "execution_probability_window_span_seconds": (
            None if window_span_seconds is None else str(window_span_seconds)
        ),
        "execution_probability_max_window_seconds": (
            None if max_window_seconds is None else str(max_window_seconds)
        ),
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
