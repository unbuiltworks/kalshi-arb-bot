"""Read-only analyzer for Gemini/Kalshi 15m threshold-gap cushion breaches.

The threshold-dominance study identifies an opposite-side orientation that cannot
both lose *if both venues observed the same terminal value*. Gemini and Kalshi do
not use the same settlement index, so this module independently checks whether the
actual numeric terminal values breached that shared-value cushion.

No order clients, credentials, approvals, or live execution paths are imported.
"""
from __future__ import annotations

from collections import defaultdict
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


def _pct(values: list[Decimal], q: Decimal) -> str | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((D(len(ordered) - 1) * q).to_integral_value(rounding="ROUND_FLOOR"))
    return str(ordered[idx])


def analyze_row(row: dict[str, Any]) -> dict[str, Any]:
    """Verify numeric finals and quantify the *directional* settlement-basis cushion.

    Missing or malformed threshold/final-value evidence never becomes a pass.
    For a shared-value-safe orientation, only basis moving in the direction that can
    defeat the threshold ordering is treated as adverse. The raw-value ratio is exact:
    adverse_terminal_basis / absolute_threshold_gap. A ratio <1 is a sufficient
    terminal-value cushion against a both-lose dominance breach for that window.
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
        "terminal_basis_signed_bps": None,
        "terminal_basis_abs_bps": None,
        "adverse_terminal_basis_value": None,
        "adverse_terminal_basis_bps": None,
        "threshold_gap_abs_value": None,
        "threshold_gap_abs_bps": row.get("threshold_gap_abs_bps"),
        "adverse_basis_to_threshold_gap_ratio": None,
        "terminal_cushion_sufficient": None,
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
    signed_basis_value = k_final - g_final
    basis_bps = signed_basis_value / g_final * D("10000")
    gap_value = k_target - g_strike
    gap_abs_value = abs(gap_value)
    gap_abs_bps = abs(gap_value / g_strike * D("10000"))

    breached = False
    adverse_value: Decimal | None = None
    if safe == DIR_GYES_KNO:
        # K running above G is the direction that can turn G-NO / K-YES and defeat
        # G-YES + K-NO's shared-value dominance.
        adverse_value = max(D("0"), signed_basis_value)
        breached = g_out == "no" and k_out == "yes"
    elif safe == DIR_KYES_GNO:
        # G running above K is the direction that can turn G-YES / K-NO and defeat
        # K-YES + G-NO's shared-value dominance.
        adverse_value = max(D("0"), -signed_basis_value)
        breached = k_out == "no" and g_out == "yes"

    ratio = None
    sufficient = None
    adverse_bps = None
    if adverse_value is not None:
        adverse_bps = adverse_value / g_final * D("10000")
        if gap_abs_value > 0:
            ratio = adverse_value / gap_abs_value
            sufficient = ratio < D("1")
        else:
            sufficient = False

    out.update({
        "evidence_complete": True,
        "numeric_gemini_outcome": g_out,
        "numeric_kalshi_outcome": k_out,
        "numeric_outcomes_agree": g_out == k_out,
        "cross_index_dominance_breached": breached,
        "terminal_basis_signed_bps": str(basis_bps),
        "terminal_basis_abs_bps": str(abs(basis_bps)),
        "adverse_terminal_basis_value": str(adverse_value) if adverse_value is not None else None,
        "adverse_terminal_basis_bps": str(adverse_bps) if adverse_bps is not None else None,
        "threshold_gap_abs_value": str(gap_abs_value),
        "threshold_gap_abs_bps": str(gap_abs_bps),
        "adverse_basis_to_threshold_gap_ratio": str(ratio) if ratio is not None else None,
        "terminal_cushion_sufficient": sufficient,
        "blocker": (
            "CROSS_INDEX_DOMINANCE_BREACH_OBSERVED"
            if breached
            else "INDEX_MISMATCH_RESEARCH_ONLY"
        ),
    })
    return out


def _asset_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in rows if row.get("evidence_complete")]
    abs_basis = [_d(row.get("terminal_basis_abs_bps")) for row in complete]
    abs_basis = [x for x in abs_basis if x is not None]
    adverse_basis = [_d(row.get("adverse_terminal_basis_bps")) for row in complete]
    adverse_basis = [x for x in adverse_basis if x is not None]
    ratios = [_d(row.get("adverse_basis_to_threshold_gap_ratio")) for row in complete]
    ratios = [x for x in ratios if x is not None]
    cushions = [row for row in complete if row.get("terminal_cushion_sufficient") is True]
    breached = [row for row in complete if row.get("cross_index_dominance_breached") is True]
    return {
        "numeric_evidence_complete": len(complete),
        "cross_index_dominance_breaches": len(breached),
        "terminal_cushion_sufficient": len(cushions),
        "terminal_cushion_sufficient_rate": (
            str(D(len(cushions)) / D(len(complete))) if complete else None
        ),
        "terminal_basis_abs_bps_p50": _pct(abs_basis, D("0.50")),
        "terminal_basis_abs_bps_p90": _pct(abs_basis, D("0.90")),
        "terminal_basis_abs_bps_p95": _pct(abs_basis, D("0.95")),
        "terminal_basis_abs_bps_p99": _pct(abs_basis, D("0.99")),
        "terminal_basis_abs_bps_max": str(max(abs_basis)) if abs_basis else None,
        "adverse_terminal_basis_bps_p95": _pct(adverse_basis, D("0.95")),
        "adverse_terminal_basis_bps_p99": _pct(adverse_basis, D("0.99")),
        "adverse_terminal_basis_bps_max": str(max(adverse_basis)) if adverse_basis else None,
        "adverse_basis_to_gap_ratio_p95": _pct(ratios, D("0.95")),
        "adverse_basis_to_gap_ratio_p99": _pct(ratios, D("0.99")),
        "adverse_basis_to_gap_ratio_max": str(max(ratios)) if ratios else None,
        "ratio_le_0_25": sum(1 for x in ratios if x <= D("0.25")),
        "ratio_le_0_50": sum(1 for x in ratios if x <= D("0.50")),
        "ratio_lt_1": sum(1 for x in ratios if x < D("1")),
    }


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    analyzed = [analyze_row(row) for row in rows]
    complete = [row for row in analyzed if row["evidence_complete"]]
    breached = [row for row in complete if row["cross_index_dominance_breached"] is True]
    by_asset_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in analyzed:
        by_asset_rows[str(row.get("asset") or "UNKNOWN")].append(row)
    return {
        "rows": len(analyzed),
        "numeric_evidence_complete": len(complete),
        "cross_index_dominance_breaches": len(breached),
        "breach_rate_complete": (
            str(D(len(breached)) / D(len(complete))) if complete else None
        ),
        "overall": _asset_summary(analyzed),
        "by_asset": {
            asset: _asset_summary(by_asset_rows.get(asset, []))
            for asset in ("BTC", "ETH", "SOL", "XRP")
        },
        "interpretation": (
            "terminal_cushion_sufficient means the observed adverse signed index basis was "
            "strictly smaller than the protective threshold gap; this is historical evidence, "
            "not a future guarantee or live authorization"
        ),
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "hard_blocker": "INDEX_MISMATCH_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
