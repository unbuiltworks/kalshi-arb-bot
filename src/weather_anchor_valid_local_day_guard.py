"""Fail-closed independent-anchor valid-time -> contract local-day binding.

A producer-declared ``independent_anchor_local_date`` does not prove that the
independent forecast anchor actually targets the exact Kalshi contract day. This
read-only helper independently derives the anchor's station-local valid date from an
explicit timezone-aware anchor valid timestamp and an audited IANA station timezone.

No credentials, order entry, live-arm state, or execution paths exist here.
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


def audit_anchor_valid_local_day(
    *,
    market_date: Any,
    independent_anchor_local_date: Any,
    independent_anchor_valid_time: Any,
    station_timezone: Any,
    timezone_mapping_verified: Any,
) -> dict[str, Any]:
    """Prove the independent anchor actually targets the exact contract local day."""
    blockers: list[str] = []
    market_day = _strict_iso_date(market_date)
    anchor_day = _strict_iso_date(independent_anchor_local_date)
    valid_dt = _aware_datetime(independent_anchor_valid_time)

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
    if anchor_day is None:
        blockers.append("INDEPENDENT_ANCHOR_LOCAL_DATE_UNVERIFIED")
    if valid_dt is None:
        blockers.append("INDEPENDENT_ANCHOR_VALID_TIME_UNVERIFIED")

    derived_day = None
    if valid_dt is not None and zone is not None:
        derived_day = valid_dt.astimezone(zone).date().isoformat()
        if market_day is not None and derived_day != market_day:
            blockers.append("INDEPENDENT_ANCHOR_VALID_TIME_MARKET_DAY_MISMATCH")
        if anchor_day is not None and derived_day != anchor_day:
            blockers.append("INDEPENDENT_ANCHOR_VALID_TIME_DECLARED_DAY_MISMATCH")

    blockers = sorted(set(blockers))
    return {
        "anchor_valid_local_day_verified": not blockers,
        "market_local_date": market_day,
        "independent_anchor_local_date": anchor_day,
        "independent_anchor_valid_time": (
            independent_anchor_valid_time if valid_dt is not None else None
        ),
        "station_timezone": timezone_name or None,
        "derived_anchor_local_date": derived_day,
        "blockers": blockers,
        "classification": (
            "INDEPENDENT_ANCHOR_VALID_TIME_LOCAL_DAY_BOUND_SHADOW"
            if not blockers
            else "INDEPENDENT_ANCHOR_VALID_TIME_LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
