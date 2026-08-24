"""Fail-closed fee-history resolver for read-only weather research.

Current Kalshi Series metadata is a present-state snapshot.  It must not be applied
retroactively to a quote observed before ``last_updated_ts`` unless a complete
historical fee-change feed establishes the fee that was effective at that quote.

No network or order-entry code lives here.
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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _valid_fee(fee_type: Any, fee_multiplier: Any) -> bool:
    if fee_type != "quadratic":
        return False
    try:
        multiplier = Decimal(str(fee_multiplier))
    except Exception:
        return False
    return multiplier.is_finite() and multiplier >= 0


def _result(
    *,
    verified: bool,
    ticker: str,
    fee_type: Any = None,
    fee_multiplier: Any = None,
    source: str | None = None,
    effective_at: datetime | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    """Return normalized resolver evidence suitable for downstream trust boundaries."""
    return {
        "verified": verified,
        "series_ticker": ticker or None,
        "fee_type": fee_type,
        "fee_multiplier": fee_multiplier,
        "source": source,
        "effective_at": _iso(effective_at),
        "blockers": sorted(set(blockers or [])),
    }


def resolve_fee_at_quote(
    series: dict[str, Any],
    *,
    fee_changes: list[dict[str, Any]] | None,
    observed_at: Any,
    historical_complete: bool = False,
) -> dict[str, Any]:
    """Resolve fee metadata without leaking present-state fees into history.

    ``historical_complete`` must be literal ``True`` before a quote older than the
    Series snapshot may use historical fee-change records.  This is intended to be
    set only when the caller queried the authoritative fee-change endpoint with
    historical records included.

    Successful results additionally expose the exact Series identity and effective
    timestamp of the fee record.  Those fields are evidence only; they do not by
    themselves prove when the caller observed the authoritative fee feed.
    """
    ticker = str(series.get("ticker") or "").strip()
    quote_time = _aware_iso(observed_at)
    series_updated = _aware_iso(series.get("last_updated_ts"))
    blockers: list[str] = []

    if not ticker or quote_time is None or series_updated is None or not isinstance(fee_changes, list):
        return _result(
            verified=False,
            ticker=ticker,
            blockers=["FEE_SCHEDULE_UNVERIFIED"],
        )

    matching: list[tuple[datetime, dict[str, Any]]] = []
    for change in fee_changes:
        if not isinstance(change, dict):
            blockers.append("FEE_SCHEDULE_UNVERIFIED")
            continue
        if str(change.get("series_ticker") or "").strip() != ticker:
            continue
        scheduled = _aware_iso(change.get("scheduled_ts"))
        if scheduled is None or not _valid_fee(change.get("fee_type"), change.get("fee_multiplier")):
            blockers.append("FEE_SCHEDULE_UNVERIFIED")
            continue
        matching.append((scheduled, change))

    if blockers:
        return _result(
            verified=False,
            ticker=ticker,
            blockers=blockers,
        )

    prior = [(ts, change) for ts, change in matching if ts <= quote_time]

    if quote_time < series_updated:
        if historical_complete is not True or not prior:
            return _result(
                verified=False,
                ticker=ticker,
                blockers=["FEE_HISTORY_UNVERIFIED"],
            )
        effective_at, effective = max(prior, key=lambda item: item[0])
        return _result(
            verified=True,
            ticker=ticker,
            fee_type=effective["fee_type"],
            fee_multiplier=effective["fee_multiplier"],
            source="historical_fee_change",
            effective_at=effective_at,
        )

    fee_type = series.get("fee_type")
    fee_multiplier = series.get("fee_multiplier")
    source = "series_current"
    effective_at = series_updated
    if not _valid_fee(fee_type, fee_multiplier):
        return _result(
            verified=False,
            ticker=ticker,
            blockers=["FEE_UNVERIFIED"],
        )

    later_effective = [
        (ts, change)
        for ts, change in matching
        if series_updated < ts <= quote_time
    ]
    if later_effective:
        effective_at, effective = max(later_effective, key=lambda item: item[0])
        fee_type = effective["fee_type"]
        fee_multiplier = effective["fee_multiplier"]
        source = "scheduled_fee_change"

    return _result(
        verified=True,
        ticker=ticker,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        source=source,
        effective_at=effective_at,
    )
