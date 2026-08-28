"""Fail-closed local-day binding for weather model SHADOW economics.

A producer-supplied ``same_local_day_verified=true`` flag is not enough to prove that
forecast/model and independent-anchor evidence target the exact Kalshi contract day.
This helper binds explicit forecast-side local dates to the exact market date before
model EV can be considered certifiable.

Read-only research helper only: no credentials, order entry, or live-arm path.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def _strict_iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    # Reject permissive/non-canonical representations.
    return parsed.isoformat() if parsed.isoformat() == text else None


def audit_weather_local_day_binding(
    *,
    market_date: Any,
    model_local_date: Any,
    independent_anchor_local_date: Any,
    same_local_day_verified: Any,
) -> dict[str, Any]:
    """Require explicit model/anchor local dates to equal the exact market date.

    This intentionally does not infer local dates from UTC timestamps because the
    applicable station timezone/local-day rule is contract-specific. The producer must
    preserve its already-audited local-date conversion explicitly.
    """
    blockers: list[str] = []
    market_day = _strict_iso_date(market_date)
    model_day = _strict_iso_date(model_local_date)
    anchor_day = _strict_iso_date(independent_anchor_local_date)

    if same_local_day_verified is not True:
        blockers.append("LOCAL_DAY_EQUIVALENCE_UNVERIFIED")
    if market_day is None:
        blockers.append("MARKET_LOCAL_DATE_UNVERIFIED")
    if model_day is None:
        blockers.append("MODEL_LOCAL_DATE_UNVERIFIED")
    if anchor_day is None:
        blockers.append("ANCHOR_LOCAL_DATE_UNVERIFIED")

    if market_day is not None and model_day is not None and model_day != market_day:
        blockers.append("FORECAST_MARKET_LOCAL_DAY_MISMATCH")
    if market_day is not None and anchor_day is not None and anchor_day != market_day:
        blockers.append("ANCHOR_MARKET_LOCAL_DAY_MISMATCH")

    blockers = sorted(set(blockers))
    return {
        "local_day_binding_verified": not blockers,
        "market_local_date": market_day,
        "model_local_date": model_day,
        "independent_anchor_local_date": anchor_day,
        "blockers": blockers,
        "classification": (
            "LOCAL_DAY_BOUND_SHADOW"
            if not blockers
            else "LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
