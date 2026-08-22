"""Fail-closed model sanity audit for read-only weather EV research.

Settlement certification and model certification are deliberately separate. This
module prevents a large model-vs-market EV from being treated as calibrated shadow
economics unless the forecast is independently anchored to the same station/local day,
the disagreement tolerance itself is audited, and that audit is bound to historical
calibration evidence available before the forecast being scored.

No execution path exists here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _parse_aware_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


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
    calibration_evidence_verified: Any = None,
    calibration_evidence_id: Any = None,
    calibration_sample_count: Any = None,
    calibration_station_id: Any = None,
    calibration_window_end: Any = None,
    model_run_time: Any = None,
) -> dict[str, Any]:
    """Return a read-only, fail-closed model sanity decision.

    A tolerance is intentionally caller-supplied rather than guessed here. It must
    itself be explicitly audited and tied to historical calibration evidence. The
    calibration window must end no later than the model run being scored, preventing
    future settlement/forecast information from leaking into the certification.
    """
    blockers: list[str] = []
    model = _finite_decimal(model_mean_f)
    anchor = _finite_decimal(independent_anchor_high_f)
    tolerance = _finite_decimal(disagreement_tolerance_f)

    model_station = str(model_station_id or "").strip().upper()
    anchor_station = str(independent_anchor_station_id or "").strip().upper()
    calibration_station = str(calibration_station_id or "").strip().upper()
    evidence_id = str(calibration_evidence_id or "").strip()

    try:
        sample_count = int(calibration_sample_count)
    except (TypeError, ValueError):
        sample_count = -1

    calibration_end = _parse_aware_iso(calibration_window_end)
    run_time = _parse_aware_iso(model_run_time)

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

    if calibration_evidence_verified is not True:
        blockers.append("MODEL_CALIBRATION_EVIDENCE_UNVERIFIED")
    if not evidence_id:
        blockers.append("MODEL_CALIBRATION_EVIDENCE_ID_MISSING")
    if sample_count <= 0:
        blockers.append("MODEL_CALIBRATION_SAMPLE_COUNT_INVALID")
    if not calibration_station or not model_station or calibration_station != model_station:
        blockers.append("MODEL_CALIBRATION_STATION_MISMATCH")
    if calibration_end is None or run_time is None:
        blockers.append("MODEL_CALIBRATION_TIME_PROVENANCE_INVALID")
    elif calibration_end > run_time:
        blockers.append("MODEL_CALIBRATION_LOOKAHEAD_DETECTED")

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
        "calibration_evidence_id": evidence_id or None,
        "calibration_sample_count": sample_count if sample_count > 0 else None,
        "calibration_station_id": calibration_station or None,
        "calibration_window_end": calibration_end.isoformat() if calibration_end is not None else None,
        "blockers": sorted(set(blockers)),
        "classification": "MODEL_SANITY_VERIFIED_SHADOW" if sane else "MODEL_DISAGREEMENT_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
