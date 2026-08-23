"""Read-only transition analysis for Gemini/Kalshi 15m settlement basis.

Empirically, each venue's next 15-minute threshold appears to inherit its own prior
terminal value. Therefore the current signed threshold gap (K target - G strike)
should closely track the previous window's signed terminal basis (K final - G final).

For the threshold-dominance-safe opposite-side orientation, cross-index basis can
only defeat that inherited cushion when the current terminal basis has the SAME
adverse sign as the current threshold gap and strictly exceeds its magnitude.
This module measures that transition geometry only. It never authorizes trading.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

D = Decimal


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _pct(values: list[Decimal], q: Decimal) -> str | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((D(len(ordered) - 1) * q).to_integral_value(rounding="ROUND_FLOOR"))
    return str(ordered[idx])


def _signed_terminal_basis(row: dict[str, Any]) -> Decimal | None:
    g = _d(row.get("gemini_settlement"))
    k = _d(row.get("kalshi_expiration_value"))
    if g is None or k is None:
        return None
    return k - g


def _signed_threshold_gap(row: dict[str, Any]) -> Decimal | None:
    g = _d(row.get("gemini_strike"))
    k = _d(row.get("kalshi_target"))
    if g is None or k is None:
        return None
    return k - g


def _reference_price(row: dict[str, Any]) -> Decimal | None:
    g = _d(row.get("gemini_strike"))
    return g if g not in (None, D("0")) else None


def analyze_transition(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    asset = str(current.get("asset") or "").upper()
    out: dict[str, Any] = {
        "asset": asset,
        "previous_expiry_utc": previous.get("expiry_utc"),
        "current_expiry_utc": current.get("expiry_utc"),
        "previous_gemini_ticker": previous.get("gemini_ticker"),
        "current_gemini_ticker": current.get("gemini_ticker"),
        "evidence_complete": False,
        "consecutive_15m": False,
        "previous_terminal_basis_value": None,
        "current_threshold_gap_value": None,
        "inheritance_residual_value": None,
        "inheritance_residual_abs_bps": None,
        "inheritance_sign_matches": None,
        "current_terminal_basis_value": None,
        "current_basis_same_sign_as_gap": None,
        "current_adverse_basis_to_gap_ratio": None,
        "current_basis_strictly_overshoots_gap": None,
        "actual_dominance_breach": None,
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    if str(previous.get("asset") or "").upper() != asset:
        out["blocker"] = "ASSET_MISMATCH"
        return out
    p_dt = _dt(previous.get("expiry_utc"))
    c_dt = _dt(current.get("expiry_utc"))
    if p_dt is None or c_dt is None:
        out["blocker"] = "MISSING_EXPIRY"
        return out
    delta_s = (c_dt - p_dt).total_seconds()
    out["consecutive_15m"] = 899 <= delta_s <= 901
    if not out["consecutive_15m"]:
        out["blocker"] = "NON_CONSECUTIVE_WINDOW"
        return out

    prior_basis = _signed_terminal_basis(previous)
    gap = _signed_threshold_gap(current)
    basis = _signed_terminal_basis(current)
    ref = _reference_price(current)
    if None in (prior_basis, gap, basis, ref):
        out["blocker"] = "MISSING_NUMERIC_EVIDENCE"
        return out
    assert prior_basis is not None and gap is not None and basis is not None and ref is not None

    residual = gap - prior_basis
    sign_matches = (
        prior_basis == 0 and gap == 0
        or prior_basis > 0 and gap > 0
        or prior_basis < 0 and gap < 0
    )
    same_adverse_sign = (
        gap != 0
        and ((gap > 0 and basis > 0) or (gap < 0 and basis < 0))
    )
    ratio = D("0")
    overshoot = False
    if gap != 0 and same_adverse_sign:
        ratio = abs(basis) / abs(gap)
        overshoot = abs(basis) > abs(gap)

    # A real breach is stronger than a basis overshoot: the two official outcomes
    # must actually be the both-lose combination for the shared-value-safe direction.
    safe = str(current.get("dominance_safe_direction_shared_value") or "")
    g_out = str(current.get("gemini_outcome") or "").lower()
    k_out = str(current.get("kalshi_outcome") or "").lower()
    actual_breach = False
    if safe == "GEMINI_YES_KALSHI_NO":
        actual_breach = g_out == "no" and k_out == "yes"
    elif safe == "KALSHI_YES_GEMINI_NO":
        actual_breach = k_out == "no" and g_out == "yes"

    out.update({
        "evidence_complete": True,
        "previous_terminal_basis_value": str(prior_basis),
        "current_threshold_gap_value": str(gap),
        "inheritance_residual_value": str(residual),
        "inheritance_residual_abs_bps": str(abs(residual) / ref * D("10000")),
        "inheritance_sign_matches": sign_matches,
        "current_terminal_basis_value": str(basis),
        "current_basis_same_sign_as_gap": same_adverse_sign,
        "current_adverse_basis_to_gap_ratio": str(ratio),
        "current_basis_strictly_overshoots_gap": overshoot,
        "actual_dominance_breach": actual_breach,
        "blocker": "INDEX_MISMATCH_RESEARCH_ONLY",
    })
    return out


def _bucket(gap_abs_bps: Decimal) -> str:
    if gap_abs_bps >= D("10"):
        return "ge_10bp"
    if gap_abs_bps >= D("5"):
        return "5_to_lt_10bp"
    if gap_abs_bps >= D("3"):
        return "3_to_lt_5bp"
    if gap_abs_bps >= D("2"):
        return "2_to_lt_3bp"
    if gap_abs_bps >= D("1"):
        return "1_to_lt_2bp"
    return "lt_1bp"


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_asset[str(row.get("asset") or "").upper()].append(row)

    transitions: list[dict[str, Any]] = []
    for asset_rows in by_asset.values():
        ordered = sorted(asset_rows, key=lambda row: _dt(row.get("expiry_utc")) or datetime.min)
        for previous, current in zip(ordered, ordered[1:]):
            transitions.append(analyze_transition(previous, current))

    complete = [row for row in transitions if row.get("evidence_complete")]
    residuals = [_d(row.get("inheritance_residual_abs_bps")) for row in complete]
    residuals = [x for x in residuals if x is not None]
    ratios = [_d(row.get("current_adverse_basis_to_gap_ratio")) for row in complete]
    ratios = [x for x in ratios if x is not None]

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {
        "transitions": 0,
        "same_adverse_sign": 0,
        "strict_basis_overshoots": 0,
        "actual_dominance_breaches": 0,
    })
    for row in complete:
        gap = _d(row.get("current_threshold_gap_value"))
        current_raw = next(
            (r for r in by_asset[row["asset"]] if str(r.get("gemini_ticker")) == str(row.get("current_gemini_ticker"))),
            None,
        )
        ref = _reference_price(current_raw or {})
        if gap is None or ref is None:
            continue
        name = _bucket(abs(gap) / ref * D("10000"))
        b = buckets[name]
        b["transitions"] += 1
        b["same_adverse_sign"] += int(bool(row.get("current_basis_same_sign_as_gap")))
        b["strict_basis_overshoots"] += int(bool(row.get("current_basis_strictly_overshoots_gap")))
        b["actual_dominance_breaches"] += int(bool(row.get("actual_dominance_breach")))

    return {
        "candidate_transitions": len(transitions),
        "complete_consecutive_15m_transitions": len(complete),
        "inheritance_sign_matches": sum(1 for row in complete if row.get("inheritance_sign_matches")),
        "inheritance_sign_match_rate": (
            str(D(sum(1 for row in complete if row.get("inheritance_sign_matches"))) / D(len(complete)))
            if complete else None
        ),
        "inheritance_residual_abs_bps_p50": _pct(residuals, D("0.50")),
        "inheritance_residual_abs_bps_p95": _pct(residuals, D("0.95")),
        "inheritance_residual_abs_bps_p99": _pct(residuals, D("0.99")),
        "inheritance_residual_abs_bps_max": str(max(residuals)) if residuals else None,
        "same_adverse_sign_transitions": sum(1 for row in complete if row.get("current_basis_same_sign_as_gap")),
        "strict_basis_overshoots": sum(1 for row in complete if row.get("current_basis_strictly_overshoots_gap")),
        "actual_dominance_breaches": sum(1 for row in complete if row.get("actual_dominance_breach")),
        "adverse_basis_to_gap_ratio_p50": _pct(ratios, D("0.50")),
        "adverse_basis_to_gap_ratio_p95": _pct(ratios, D("0.95")),
        "adverse_basis_to_gap_ratio_p99": _pct(ratios, D("0.99")),
        "by_current_gap_bucket": dict(buckets),
        "interpretation": (
            "If current threshold gap inherits prior terminal basis, the shared-value-safe "
            "orientation can only lose both legs when current terminal basis keeps the "
            "gap's adverse sign and strictly exceeds its magnitude; actual outcome breach "
            "is checked separately. Historical evidence never authorizes live trading."
        ),
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
