"""Fail-closed weather portfolio metric binding to time-bound canonical OPEN records.

V4 proves counted execution-probability episode IDs correspond to explicit canonical
read-only OPEN records in the exact market/side/quantity/price/min-edge cohort. V5
additionally proves each OPEN record belongs to the audited empirical observation
window and existed no later than the quote being ranked. This prevents producer-supplied
OPEN records from being backfilled with later observations while still passing the
aggregate window anti-lookahead check.

Research-only. No order-entry or live-probe path exists here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.weather_portfolio_metric_binding_guard_v4 import (
    audit_weather_portfolio_metric_binding_v4,
)

EPISODE_RECORD_TIME_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_RECORD_TIME_UNVERIFIED"
EPISODE_RECORD_LOOKAHEAD = "WEATHER_EXECUTION_EPISODE_RECORD_LOOKAHEAD_DETECTED"
EPISODE_RECORD_OUTSIDE_WINDOW = "WEATHER_EXECUTION_EPISODE_RECORD_OUTSIDE_WINDOW"


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _strict_ms(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def audit_weather_portfolio_metric_binding_v5(row: dict[str, Any]) -> dict[str, Any]:
    """Require every counted OPEN record to be inside the audited pre-quote window."""
    base = audit_weather_portfolio_metric_binding_v4(row)
    blockers = list(base.get("blockers") or [])

    quote_ms = _iso_ms(row.get("quote_observed_at"))
    window_start_ms = _iso_ms(row.get("executable_probability_window_start"))
    window_end_ms = _iso_ms(row.get("executable_probability_window_end"))
    records = row.get("executable_probability_episode_records")

    window_valid = (
        quote_ms is not None
        and window_start_ms is not None
        and window_end_ms is not None
        and window_start_ms <= window_end_ms <= quote_ms
        and isinstance(records, (list, tuple))
        and bool(records)
    )

    record_times_valid = window_valid
    lookahead = False
    outside_window = False

    if record_times_valid:
        for record in records:
            if not isinstance(record, dict):
                record_times_valid = False
                break
            observed_ms = _strict_ms(record.get("observed_ms"))
            opened_ms = _strict_ms(record.get("opened_ms"))
            # Canonical weather OPEN records are created at the same observation that
            # opens the episode. Requiring equality prevents a producer from attaching
            # a later sample to an older episode identity.
            if observed_ms is None or opened_ms is None or observed_ms != opened_ms:
                record_times_valid = False
                break
            if observed_ms > quote_ms:
                lookahead = True
            if observed_ms < window_start_ms or observed_ms > window_end_ms:
                outside_window = True

    if not record_times_valid:
        blockers.append(EPISODE_RECORD_TIME_UNVERIFIED)
    if lookahead:
        blockers.append(EPISODE_RECORD_LOOKAHEAD)
    if outside_window:
        blockers.append(EPISODE_RECORD_OUTSIDE_WINDOW)

    verified = (
        base.get("weather_portfolio_metric_binding_verified") is True
        and record_times_valid
        and not lookahead
        and not outside_window
    )

    return {
        **base,
        "weather_portfolio_metric_binding_verified": verified,
        "weather_execution_episode_record_time_verified": verified,
        "weather_execution_episode_record_lookahead_detected": lookahead,
        "weather_execution_episode_record_outside_window": outside_window,
        "blockers": list(dict.fromkeys(blockers)),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
