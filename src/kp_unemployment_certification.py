"""Exact Kalshi <-> Polymarket US certification for August 2026 U-3 thresholds.

This module is intentionally tiny and event-specific.  It does not infer equivalence
from market titles.  The approved contracts share the same audited semantics:
seasonally adjusted U-3, August 2026, BLS Employment Situation, strict > threshold,
subsequent revisions ignored, and a three-month missing-data fallback to the most
recent previous month with available data.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class U3Approval:
    threshold: Decimal
    settlement_key: str
    polymarket_slug: str
    kalshi_ticker: str
    fingerprint: str


BASE_RULE = {
    "statistic": "US seasonally adjusted unemployment rate U-3",
    "reference_month": "2026-08",
    "source": "BLS Employment Situation Report",
    "comparator": "strictly_greater_than",
    "revision_policy": "ignore_subsequent_revisions",
    "missing_data_horizon": "3_months_after_scheduled_release",
    "missing_data_fallback": "most_recent_previous_month_with_available_data",
}


def _fingerprint(threshold: str) -> str:
    body = {**BASE_RULE, "threshold_percent": threshold}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


APPROVALS: tuple[U3Approval, ...] = tuple(
    U3Approval(
        threshold=Decimal(threshold),
        settlement_key=f"macro|us|unemployment_u3|2026-08|GT|{threshold}",
        polymarket_slug=f"urc-usunemp-sa-august-2026-09-04-gt{threshold.replace('.', 'pt')}pct",
        kalshi_ticker=f"KXU3-26AUG-T{threshold}",
        fingerprint=_fingerprint(threshold),
    )
    for threshold in ("4.0", "4.2", "4.4")
)
BY_SLUG = {row.polymarket_slug: row for row in APPROVALS}
BY_TICKER = {row.kalshi_ticker: row for row in APPROVALS}


def validate_poly_market(approval: U3Approval, market: dict[str, Any]) -> None:
    """Fail closed if the live Polymarket US contract text drifts from the audit."""
    if str(market.get("slug") or "") != approval.polymarket_slug:
        raise PermissionError("Polymarket slug changed")
    if market.get("active") is False or market.get("closed") is True or market.get("archived") is True:
        raise PermissionError("Polymarket contract is not active")
    text = " ".join(str(market.get(k) or "") for k in ("question", "title", "description")).lower()
    required = (
        "seasonally adjusted unemployment rate (u-3)",
        "bureau of labor statistics",
        "employment situation report for august 2026",
        f"above {approval.threshold}%",
        "subsequent revisions",
        "will not be considered",
        "within three months",
        "most recent previous month",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise PermissionError(f"Polymarket rule fingerprint drift: {missing}")


def validate_kalshi_market(approval: U3Approval, market: dict[str, Any]) -> None:
    """Validate exact live identity/threshold; full lifecycle semantics are audit-hashed."""
    if str(market.get("ticker") or "") != approval.kalshi_ticker:
        raise PermissionError("Kalshi ticker changed")
    status = str(market.get("status") or "").lower()
    if status not in {"open", "active"}:
        raise PermissionError(f"Kalshi contract is not open: {status or 'unknown'}")
    text = " ".join(str(market.get(k) or "") for k in ("title", "subtitle", "yes_sub_title", "rules_primary")).lower()
    if "unemployment" not in text:
        raise PermissionError("Kalshi live identity no longer says unemployment")
    threshold_tokens = (f"{approval.threshold}%", f"{approval.threshold}")
    floor = str(market.get("floor_strike") or market.get("floor_strike_str") or "")
    if not any(token in text or token == floor for token in threshold_tokens):
        raise PermissionError("Kalshi threshold no longer matches approval")


def approval_dicts() -> list[dict[str, Any]]:
    return [
        {
            "settlement_key": row.settlement_key,
            "polymarket_slug": row.polymarket_slug,
            "kalshi_ticker": row.kalshi_ticker,
            "polymarket_source_side": "CONTRACT",
            "kalshi_source_side": "CONTRACT",
            "rule_fingerprint_sha256": row.fingerprint,
            "approved_for_execution": True,
            "max_quantity": 1,
            "mode": "LIVE_PROBE",
        }
        for row in APPROVALS
    ]
