"""Fail-closed model sanity audit for read-only weather EV research.

Settlement certification and model certification are deliberately separate. This
module prevents a large model-vs-market EV from being treated as calibrated shadow
economics unless the forecast is independently anchored to the same station/local day,
the disagreement tolerance itself is audited, and that audit is bound to historical
calibration evidence available before the forecast being scored.

The independent anchor must also prove provider/model provenance. A bare source label is
not sufficient: the anchor needs an HTTPS source URL bound to its declared provider, and
its provider/model must be independent of the forecast model (including declared
ensemble component providers).

No execution path exists here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urlparse


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


def _provider_family(value: Any) -> str:
    """Normalize provider labels to an organizational independence family."""
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "NWS": "NOAA",
        "NOAA": "NOAA",
        "GFS": "NOAA",
        "NOAA_GFS": "NOAA",
        "ECMWF": "ECMWF",
        "IFS": "ECMWF",
        "DWD": "DWD",
        "ICON": "DWD",
        "TWC": "TWC",
        "THE_WEATHER_COMPANY": "TWC",
        "WEATHER_COMPANY": "TWC",
        "WEATHER.COM": "TWC",
    }
    return aliases.get(text, text)


def _provider_list(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw: Iterable[Any] = [part for part in values.split(",") if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw = values
    else:
        return ()
    return tuple(sorted({_provider_family(v) for v in raw if _provider_family(v)}))


def _official_anchor_url_matches_provider(url: Any, provider: Any) -> bool:
    """Require HTTPS provenance structurally bound to the declared forecast provider."""
    text = str(url or "").strip()
    family = _provider_family(provider)
    if not text or not family:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")

    def host_is(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if family == "NOAA":
        return host_is("weather.gov") or host_is("noaa.gov")
    if family == "TWC":
        return host_is("weather.com")
    if family == "ECMWF":
        return host_is("ecmwf.int")
    if family == "DWD":
        return host_is("dwd.de")
    return False


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
    model_id: Any = None,
    model_provider: Any = None,
    model_component_providers: Any = None,
    calibration_model_id: Any = None,
    calibration_target: Any = None,
    calibration_evidence_verified: Any = None,
    calibration_evidence_id: Any = None,
    calibration_sample_count: Any = None,
    calibration_station_id: Any = None,
    calibration_window_end: Any = None,
    model_run_time: Any = None,
    quote_observed_at: Any = None,
    independent_anchor_source_id: Any = None,
    independent_anchor_provider: Any = None,
    independent_anchor_model_id: Any = None,
    independent_anchor_provenance_url: Any = None,
    independent_anchor_observed_at: Any = None,
) -> dict[str, Any]:
    """Return a read-only, fail-closed model sanity decision.

    A tolerance is intentionally caller-supplied rather than guessed here. It must
    itself be explicitly audited and tied to historical calibration evidence. The
    calibration window must end no later than the model run being scored, preventing
    future settlement/forecast information from leaking into the certification.

    Calibration evidence is also bound to the exact model identity and target metric.
    Independent anchor evidence must carry structural provider/model provenance, must
    come from a provider family outside the scored model/ensemble, and must have been
    observed no later than the quote being scored. This prevents arbitrary source
    strings, same-model/same-provider derivative anchors, or later forecast updates from
    retrospectively certifying an earlier apparent edge.
    """
    blockers: list[str] = []
    model = _finite_decimal(model_mean_f)
    anchor = _finite_decimal(independent_anchor_high_f)
    tolerance = _finite_decimal(disagreement_tolerance_f)

    model_station = str(model_station_id or "").strip().upper()
    anchor_station = str(independent_anchor_station_id or "").strip().upper()
    calibration_station = str(calibration_station_id or "").strip().upper()
    evidence_id = str(calibration_evidence_id or "").strip()
    model_identity = str(model_id or "").strip()
    calibration_model_identity = str(calibration_model_id or "").strip()
    calibration_target_text = str(calibration_target or "").strip().upper()
    anchor_source_id = str(independent_anchor_source_id or "").strip()
    anchor_model_identity = str(independent_anchor_model_id or "").strip()
    anchor_provider_family = _provider_family(independent_anchor_provider)
    model_provider_family = _provider_family(model_provider)
    component_provider_families = set(_provider_list(model_component_providers))
    if model_provider_family:
        component_provider_families.add(model_provider_family)
    anchor_url = str(independent_anchor_provenance_url or "").strip()

    try:
        sample_count = int(calibration_sample_count)
    except (TypeError, ValueError):
        sample_count = -1

    calibration_end = _parse_aware_iso(calibration_window_end)
    run_time = _parse_aware_iso(model_run_time)
    quote_time = _parse_aware_iso(quote_observed_at)
    anchor_observed = _parse_aware_iso(independent_anchor_observed_at)

    if model is None or anchor is None:
        blockers.append("MODEL_OR_ANCHOR_VALUE_INVALID")
    if not model_station or not anchor_station or model_station != anchor_station:
        blockers.append("FORECAST_STATION_MISMATCH")
    if independent_anchor_verified is not True:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_UNVERIFIED")
    if same_local_day_verified is not True:
        blockers.append("FORECAST_LOCAL_DAY_UNVERIFIED")
    if not anchor_source_id:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_SOURCE_MISSING")
    if not anchor_provider_family:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_PROVIDER_MISSING")
    if not anchor_model_identity:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_MODEL_ID_MISSING")
    if not anchor_url or not _official_anchor_url_matches_provider(anchor_url, anchor_provider_family):
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_PROVENANCE_INVALID")
    if model_identity and anchor_model_identity and model_identity == anchor_model_identity:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_SAME_MODEL")
    if anchor_provider_family and anchor_provider_family in component_provider_families:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_PROVIDER_NOT_INDEPENDENT")
    if anchor_observed is None or quote_time is None:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_TIME_PROVENANCE_INVALID")
    elif anchor_observed > quote_time:
        blockers.append("INDEPENDENT_FORECAST_ANCHOR_LOOKAHEAD_DETECTED")
    if tolerance is None or tolerance < 0:
        blockers.append("MODEL_DISAGREEMENT_TOLERANCE_INVALID")
    if disagreement_tolerance_audited is not True:
        blockers.append("MODEL_DISAGREEMENT_TOLERANCE_UNAUDITED")
    if model_calibration_verified is not True:
        blockers.append("MODEL_CALIBRATION_UNVERIFIED")

    if not model_identity or not calibration_model_identity:
        blockers.append("MODEL_CALIBRATION_MODEL_ID_MISSING")
    elif model_identity != calibration_model_identity:
        blockers.append("MODEL_CALIBRATION_MODEL_ID_MISMATCH")
    if calibration_target_text != "DAILY_MAX_TEMPERATURE_F":
        blockers.append("MODEL_CALIBRATION_TARGET_MISMATCH")
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
        "model_id": model_identity or None,
        "model_provider": model_provider_family or None,
        "model_component_providers": sorted(component_provider_families),
        "calibration_model_id": calibration_model_identity or None,
        "calibration_target": calibration_target_text or None,
        "calibration_evidence_id": evidence_id or None,
        "calibration_sample_count": sample_count if sample_count > 0 else None,
        "calibration_station_id": calibration_station or None,
        "calibration_window_end": calibration_end.isoformat() if calibration_end is not None else None,
        "independent_anchor_source_id": anchor_source_id or None,
        "independent_anchor_provider": anchor_provider_family or None,
        "independent_anchor_model_id": anchor_model_identity or None,
        "independent_anchor_provenance_url": anchor_url or None,
        "independent_anchor_observed_at": anchor_observed.isoformat() if anchor_observed is not None else None,
        "blockers": sorted(set(blockers)),
        "classification": "MODEL_SANITY_VERIFIED_SHADOW" if sane else "MODEL_DISAGREEMENT_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
