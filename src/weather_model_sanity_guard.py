"""Fail-closed model sanity audit for read-only weather EV research.

Settlement certification and model certification are deliberately separate.  This
module prevents a large model-vs-market EV from being treated as calibrated
shadow economics unless the forecast is independently anchored to the same
station/local day and the allowed disagreement tolerance itself is audited.

No execution path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def audit_weather_model_sanity(
    *,
    model_mean_f: Any,
    model_station_id: Any,
    independent_anchor_high_f: Any,
    independent_anchor_station_id: Any,
    independent_anchor_verified: Any,
    same_local_day_verified: Any,
    disagreement_tolerance_f: Any,
    disagreement_tolerance_audited: Any,
    model_calibration_verified: Any,
) -> dict[str, Any]:
    """Return a read-only, fail-closed model sanity decision.

    A tolerance is intentionally caller-supplied rather than guessed here.  It
    must itself be explicitly audited before it can certify model sanity.
    """
    blockers: list[str] = []
    model = _finite_decimal(model_mean_f)
    anchor = _finite_decimal(independent_anchor_high_f)
    tolerance = _finite_decimal(disagreement_tolerance_f)

    model_station = str(model_station_id or "").strip().upper()
    anchor_station = str(independent_anchor_station_id or "").strip().upper()

    if model is None or anchor is None:
        blockers.append("MODEL_OR_ANCHOR_VALUE_INVALID")
    if not model_station or not anchor_station or model_station != anchor_station:
        blockers.append("FORECAST_STATION_MISMATCH")
    if independent_anchor_verified is not True:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_UNVERIFIED")
    if same_local_day_verified is not True:
        blockers.append("FORECAST_LOCAL_DAY_UNVERIFIED")
    if tolerance is None or tolerance < 0:
        blockers.append("MODEL_DISAGREEMENT_TOLERANCE_INVALID")
    if disagreement_tolerance_audited is not True:
        blockers.append("MODEL_DISAGREEMENT_TOLERANCE_UNAUDITED")
    if model_calibration_verified is not True:
        blockers.append("MODEL_CALIBRATION_UNVERIFIED")

    discrepancy = None
    if model is not None and anchor is not None:
        discrepancy = abs(model - anchor)
        if tolerance is not None and tolerance >= 0 and discrepancy > tolerance:
            blockers.append("MODEL_ANCHOR_DISAGREEMENT_EXCEEDS_AUDITED_TOLERANCE")

    sane = not blockers
    return {
        "model_sanity_verified": sane,
        "model_anchor_discrepancy_f": str(discrepancy) if discrepancy is not None else None,
        "disagreement_tolerance_f": str(tolerance) if tolerance is not None else None,
        "blockers": sorted(set(blockers)),
        "classification": "MODEL_SANITY_VERIFIED_SHADOW" if sane else "MODEL_DISAGREEMENT_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
