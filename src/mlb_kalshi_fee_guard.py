"""Fail-closed quote-time Kalshi fee audit for the MLB prop shadow.

Read-only research helper.  The MLB scanner must not score after-fee paper plans
from Series metadata alone because Kalshi publishes scheduled/historical Series
fee changes separately at ``GET /series/fee_changes``.  This guard requires both
sources to agree at the quote timestamp before returning a usable fee.

It never authorizes order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

_ALLOWED_FEE_TYPES = {"quadratic", "quadratic_with_maker_fees"}


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


def _finite_multiplier(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal("100"):
        return None
    return parsed


def audit_current_series_fee(
    *,
    series_ticker: str,
    series_meta: dict[str, Any] | None,
    fee_changes_body: dict[str, Any] | None,
    observed_at: str,
) -> dict[str, Any]:
    """Verify current Series fee metadata against the authoritative change feed.

    ``fee_changes_body`` must come from ``GET /series/fee_changes`` with
    ``show_historical=true`` for the exact Series.  The latest change effective at
    or before ``observed_at`` must agree with current Series metadata.  A future
    scheduled change is retained for telemetry but does not alter the current fee.
    Any malformed/incomplete/disagreeing evidence fails closed.
    """
    series = str(series_ticker or "").strip().upper()
    blockers: list[str] = []
    observed = _aware_iso(observed_at)
    meta = series_meta if isinstance(series_meta, dict) else {}
    fee_type = str(meta.get("fee_type") or "").strip()
    multiplier = _finite_multiplier(meta.get("fee_multiplier"))

    if not series:
        blockers.append("FEE_SERIES_TICKER_MISSING")
    if observed is None:
        blockers.append("FEE_QUOTE_TIME_INVALID_OR_NAIVE")
    if fee_type not in _ALLOWED_FEE_TYPES:
        blockers.append("FEE_TYPE_UNSUPPORTED_OR_MISSING")
    if multiplier is None:
        blockers.append("FEE_MULTIPLIER_INVALID_OR_MISSING")

    if not isinstance(fee_changes_body, dict) or not isinstance(
        fee_changes_body.get("series_fee_change_arr"), list
    ):
        blockers.append("FEE_CHANGE_FEED_MISSING_OR_MALFORMED")
        changes: list[dict[str, Any]] = []
    else:
        changes = []
        for raw in fee_changes_body["series_fee_change_arr"]:
            if not isinstance(raw, dict):
                blockers.append("FEE_CHANGE_RECORD_MALFORMED")
                continue
            record_series = str(raw.get("series_ticker") or "").strip().upper()
            if record_series != series:
                blockers.append("FEE_CHANGE_SERIES_MISMATCH")
                continue
            scheduled = _aware_iso(raw.get("scheduled_ts"))
            record_type = str(raw.get("fee_type") or "").strip()
            record_multiplier = _finite_multiplier(raw.get("fee_multiplier"))
            if scheduled is None or record_type not in _ALLOWED_FEE_TYPES or record_multiplier is None:
                blockers.append("FEE_CHANGE_RECORD_INVALID")
                continue
            changes.append(
                {
                    "scheduled": scheduled,
                    "scheduled_ts": raw.get("scheduled_ts"),
                    "fee_type": record_type,
                    "fee_multiplier": record_multiplier,
                    "id": raw.get("id"),
                }
            )

    effective = None
    next_change = None
    if observed is not None and changes:
        past = [row for row in changes if row["scheduled"] <= observed]
        future = [row for row in changes if row["scheduled"] > observed]
        if past:
            effective = max(past, key=lambda row: row["scheduled"])
        if future:
            next_change = min(future, key=lambda row: row["scheduled"])

    if effective is not None and multiplier is not None:
        if effective["fee_type"] != fee_type or effective["fee_multiplier"] != multiplier:
            blockers.append("CURRENT_SERIES_FEE_DISAGREES_WITH_EFFECTIVE_CHANGE")

    blockers = sorted(set(blockers))
    return {
        "verified": not blockers,
        "blockers": blockers,
        "series_ticker": series,
        "fee_type": fee_type if fee_type in _ALLOWED_FEE_TYPES else None,
        "fee_multiplier": str(multiplier) if multiplier is not None else None,
        "fee_source": "series_current_plus_fee_changes" if not blockers else None,
        "effective_change_id": effective.get("id") if effective else None,
        "effective_change_scheduled_ts": effective.get("scheduled_ts") if effective else None,
        "next_change_scheduled_ts": next_change.get("scheduled_ts") if next_change else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
