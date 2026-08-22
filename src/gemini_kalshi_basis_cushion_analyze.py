"""Read-only analyzer for Gemini/Kalshi 15m threshold-gap cushion breaches.

The threshold-dominance study identifies an opposite-side orientation that cannot
both lose *if both venues observed the same terminal value*. Gemini and Kalshi do
not use the same settlement index, so this module independently checks whether the
actual numeric terminal values breached that shared-value cushion.

No order clients, credentials, approvals, or live execution paths are imported.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

D = Decimal
DIR_KYES_GNO = "KALSHI_YES_GEMINI_NO"
DIR_GYES_KNO = "GEMINI_YES_KALSHI_NO"


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _outcome(value: Decimal, threshold: Decimal) -> str:
    # Both current 15m lanes resolve YES at-or-above their own threshold.
    return "yes" if value >= threshold else "no"


def analyze_row(row: dict[str, Any]) -> dict[str, Any]:
    """Verify numeric finals and determine whether index divergence defeated the
    shared-value dominance orientation.

    Missing or malformed threshold/final-value evidence never becomes a pass.
    """
    g_strike = _d(row.get("gemini_strike"))
    k_target = _d(row.get("kalshi_target"))
    g_final = _d(row.get("gemini_settlement"))
    k_final = _d(row.get("kalshi_expiration_value"))
    safe = row.get("dominance_safe_direction_shared_value")

    out = {
        "asset": str(row.get("asset") or "").upper(),
        "gemini_ticker": row.get("gemini_ticker"),
        "kalshi_ticker": row.get("kalshi_ticker"),
        "evidence_complete": False,
        "numeric_gemini_outcome": None,
        "numeric_kalshi_outcome": None,
        "numeric_outcomes_agree": None,
        "shared_value_safe_direction": safe,
        "cross_index_dominance_breached": None,
        "terminal_basis_abs_bps": None,
        "threshold_gap_abs_bps": row.get("threshold_gap_abs_bps"),
        "basis_to_threshold_gap_ratio": None,
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    if None in (g_strike, k_target, g_final, k_final):
        out["blocker"] = "MISSING_NUMERIC_THRESHOLD_OR_FINAL_VALUE"
        return out
    if g_strike == 0 or g_final == 0:
        out["blocker"] = "INVALID_ZERO_REFERENCE_VALUE"
        return out
    if safe not in (DIR_GYES_KNO, DIR_KYES_GNO, "SYMMETRIC_EQUAL_THRESHOLD"):
        out["blocker"] = "UNKNOWN_SHARED_VALUE_DIRECTION"
        return out

    g_out = _outcome(g_final, g_strike)
    k_out = _outcome(k_final, k_target)
    basis_bps = (k_final - g_final) / g_final * D("10000")
    gap_abs_bps = abs((k_target - g_strike) / g_strike * D("10000"))

    breached = False
    if safe == DIR_GYES_KNO:
        breached = g_out == "no" and k_out == "yes"
    elif safe == DIR_KYES_GNO:
        breached = k_out == "no" and g_out == "yes"

    out.update({
        "evidence_complete": True,
        "numeric_gemini_outcome": g_out,
        "numeric_kalshi_outcome": k_out,
        "numeric_outcomes_agree": g_out == k_out,
        "cross_index_dominance_breached": breached,
        "terminal_basis_abs_bps": str(abs(basis_bps)),
        "threshold_gap_abs_bps": str(gap_abs_bps),
        "basis_to_threshold_gap_ratio": (
            str(abs(basis_bps) / gap_abs_bps) if gap_abs_bps > 0 else None
        ),
        "blocker": (
            "CROSS_INDEX_DOMINANCE_BREACH_OBSERVED"
            if breached
            else "INDEX_MISMATCH_RESEARCH_ONLY"
        ),
    })
    return out


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    analyzed = [analyze_row(row) for row in rows]
    complete = [row for row in analyzed if row["evidence_complete"]]
    breached = [row for row in complete if row["cross_index_dominance_breached"] is True]
    ratios = [
        _d(row.get("basis_to_threshold_gap_ratio"))
        for row in complete
        if row.get("basis_to_threshold_gap_ratio") is not None
    ]
    ratios = [x for x in ratios if x is not None]
    return {
        "rows": len(analyzed),
        "numeric_evidence_complete": len(complete),
        "cross_index_dominance_breaches": len(breached),
        "breach_rate_complete": (
            str(D(len(breached)) / D(len(complete))) if complete else None
        ),
        "max_basis_to_threshold_gap_ratio": str(max(ratios)) if ratios else None,
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "hard_blocker": "INDEX_MISMATCH_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
