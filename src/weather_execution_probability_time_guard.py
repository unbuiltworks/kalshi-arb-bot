"""Fail-closed anti-lookahead guard for weather execution-probability cohorts.

Portfolio execution probability must be estimated only from observations that existed
at or before the quote being ranked. A historical cohort that includes later snapshots
would leak future market-liquidity information into the current SHADOW score even if
market/side/quantity/price/edge scope is otherwise exact.

This helper is read-only and cannot authorize order entry or a live probe.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

EXECUTION_SAMPLE_TIME_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED"
EXECUTION_SAMPLE_LOOKAHEAD_DETECTED = "WEATHER_EXECUTION_SAMPLE_LOOKAHEAD_DETECTED"


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


def audit_weather_execution_probability_time_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Bind an empirical execution cohort to information available by quote time."""
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
    if structurally_verified and end > quote:
        lookahead = True
        blockers.append(EXECUTION_SAMPLE_LOOKAHEAD_DETECTED)

    verified = structurally_verified and not lookahead
    return {
        "weather_execution_probability_time_verified": verified,
        "quote_observed_at": None if quote is None else quote.isoformat(),
        "execution_probability_window_start": None if start is None else start.isoformat(),
        "execution_probability_window_end": None if end is None else end.isoformat(),
        "execution_probability_lookahead_detected": lookahead,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
