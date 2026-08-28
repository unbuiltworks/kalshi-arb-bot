"""Fail-closed forecast valid-time -> contract local-day binding for weather SHADOW EV.

An explicit ``model_local_date`` is useful provenance, but by itself it does not prove
that the forecast's actual valid timestamp belongs to the exact Kalshi contract day in
the settlement station's timezone. This helper independently derives that local date
from a timezone-aware forecast valid timestamp and an explicit IANA station timezone.

Read-only research helper only: no credentials, order entry, or live-arm path.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _strict_iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == text else None


def _aware_datetime(value: Any) -> datetime | None:
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


def audit_forecast_valid_local_day(
    *,
    market_date: Any,
    model_local_date: Any,
    forecast_valid_time: Any,
    station_timezone: Any,
    timezone_mapping_verified: Any,
) -> dict[str, Any]:
    """Bind forecast valid-time to the exact market day in station-local time.

    The caller must supply an explicit IANA timezone for the settlement station and a
    literal verification flag showing that station->timezone mapping was audited.
    Stringly truthy values fail closed. No timezone is inferred from city/ticker names.
    """
    blockers: list[str] = []
    market_day = _strict_iso_date(market_date)
    model_day = _strict_iso_date(model_local_date)
    valid_dt = _aware_datetime(forecast_valid_time)

    timezone_name = station_timezone.strip() if isinstance(station_timezone, str) else ""
    zone = None
    if timezone_mapping_verified is not True:
        blockers.append("STATION_TIMEZONE_MAPPING_UNVERIFIED")
    if not timezone_name:
        blockers.append("STATION_TIMEZONE_UNVERIFIED")
    else:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            blockers.append("STATION_TIMEZONE_INVALID")

    if market_day is None:
        blockers.append("MARKET_LOCAL_DATE_UNVERIFIED")
    if model_day is None:
        blockers.append("MODEL_LOCAL_DATE_UNVERIFIED")
    if valid_dt is None:
        blockers.append("FORECAST_VALID_TIME_UNVERIFIED")

    derived_day = None
    if valid_dt is not None and zone is not None:
        derived_day = valid_dt.astimezone(zone).date().isoformat()
        if market_day is not None and derived_day != market_day:
            blockers.append("FORECAST_VALID_TIME_MARKET_DAY_MISMATCH")
        if model_day is not None and derived_day != model_day:
            blockers.append("FORECAST_VALID_TIME_MODEL_DAY_MISMATCH")

    blockers = sorted(set(blockers))
    return {
        "forecast_valid_local_day_verified": not blockers,
        "market_local_date": market_day,
        "model_local_date": model_day,
        "forecast_valid_time": forecast_valid_time if valid_dt is not None else None,
        "station_timezone": timezone_name or None,
        "derived_forecast_local_date": derived_day,
        "blockers": blockers,
        "classification": (
            "FORECAST_VALID_TIME_LOCAL_DAY_BOUND_SHADOW"
            if not blockers
            else "FORECAST_VALID_TIME_LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
