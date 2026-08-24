"""Fail-closed cross-check for weather model audit-chain telemetry.

The settlement-certified v2 producer carries detailed read-only audit envelopes for
model sanity, provider provenance, local-day binding, valid-time binding, and anti-
lookahead timing. Central reporting must not accept top-level booleans as substitutes
for those underlying audits. This helper validates that the full audit chain is present,
internally coherent, and still bound to the exact contract day before model probability
can contribute to SHADOW portfolio economics.

This module is intentionally read-only and cannot authorize order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


_AUDIT_SPECS = (
    ("model_sanity_audit", "model_sanity_verified", "model_sanity_verified"),
    (
        "model_provider_provenance_audit",
        "provider_provenance_verified",
        "model_provider_provenance_verified",
    ),
    ("local_day_binding_audit", "local_day_binding_verified", "local_day_binding_verified"),
    (
        "forecast_valid_local_day_audit",
        "forecast_valid_local_day_verified",
        "forecast_valid_local_day_verified",
    ),
    (
        "anchor_valid_local_day_audit",
        "anchor_valid_local_day_verified",
        "anchor_valid_local_day_verified",
    ),
    (
        "model_run_quote_time_audit",
        "model_run_quote_time_verified",
        "model_run_quote_time_verified",
    ),
)


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


def _safe_envelope(audit: Any, verified_key: str) -> bool:
    if not isinstance(audit, dict):
        return False
    blockers = audit.get("blockers")
    return (
        audit.get(verified_key) is True
        and isinstance(blockers, list)
        and not blockers
        and audit.get("read_only") is True
        and audit.get("order_entry") is False
        and audit.get("live_probe_ready") is False
    )


def audit_weather_model_audit_chain(row: dict[str, Any]) -> dict[str, Any]:
    """Require the producer's full underlying model audit chain, not summary booleans."""
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    market_date = row.get("market_date")

    for audit_field, verified_key, top_level_key in _AUDIT_SPECS:
        audit = row.get(audit_field)
        if not _safe_envelope(audit, verified_key):
            blockers.append(f"MODEL_AUDIT_ENVELOPE_INVALID:{audit_field}")
            continue
        if row.get(top_level_key) is not True:
            blockers.append(f"MODEL_AUDIT_TOP_LEVEL_MISMATCH:{top_level_key}")

    sanity = row.get("model_sanity_audit")
    if isinstance(sanity, dict):
        required = (
            "model_id",
            "model_provider",
            "calibration_model_id",
            "calibration_evidence_id",
            "calibration_station_id",
            "calibration_window_end",
            "independent_anchor_source_id",
            "independent_anchor_provider",
            "independent_anchor_model_id",
            "independent_anchor_provenance_url",
            "independent_anchor_observed_at",
        )
        if any(not sanity.get(key) for key in required):
            blockers.append("MODEL_SANITY_PROVENANCE_INCOMPLETE")
        if sanity.get("calibration_target") != "DAILY_MAX_TEMPERATURE_F":
            blockers.append("MODEL_SANITY_CALIBRATION_TARGET_MISMATCH")
        sample_count = sanity.get("calibration_sample_count")
        if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count <= 0:
            blockers.append("MODEL_SANITY_CALIBRATION_SAMPLE_COUNT_INVALID")

        model_id = str(sanity.get("model_id") or "").strip()
        calibration_model_id = str(sanity.get("calibration_model_id") or "").strip()
        if model_id and calibration_model_id and model_id != calibration_model_id:
            blockers.append("MODEL_SANITY_CALIBRATION_MODEL_ID_MISMATCH")

        calibration_end = _aware_iso(sanity.get("calibration_window_end"))
        anchor_observed = _aware_iso(sanity.get("independent_anchor_observed_at"))
        if calibration_end is None:
            blockers.append("MODEL_SANITY_CALIBRATION_TIME_INVALID")
        if anchor_observed is None:
            blockers.append("MODEL_SANITY_ANCHOR_OBSERVED_TIME_INVALID")

    provider = row.get("model_provider_provenance_audit")
    if isinstance(provider, dict):
        model_family = provider.get("model_provider_family")
        anchor_family = provider.get("independent_anchor_provider_family")
        component_families = provider.get("model_component_provider_families")
        if not model_family:
            blockers.append("MODEL_PROVIDER_FAMILY_MISSING")
        if not anchor_family:
            blockers.append("ANCHOR_PROVIDER_FAMILY_MISSING")
        if not isinstance(component_families, list):
            blockers.append("MODEL_COMPONENT_PROVIDER_FAMILIES_INVALID")
        elif anchor_family and anchor_family in component_families:
            blockers.append("ANCHOR_PROVIDER_NOT_INDEPENDENT")

        if isinstance(sanity, dict):
            sanity_model_provider = str(sanity.get("model_provider") or "").strip()
            sanity_anchor_provider = str(sanity.get("independent_anchor_provider") or "").strip()
            if sanity_model_provider and model_family and sanity_model_provider != model_family:
                blockers.append("MODEL_PROVIDER_AUDIT_CROSSCHECK_MISMATCH")
            if sanity_anchor_provider and anchor_family and sanity_anchor_provider != anchor_family:
                blockers.append("ANCHOR_PROVIDER_AUDIT_CROSSCHECK_MISMATCH")

    local_day = row.get("local_day_binding_audit")
    if isinstance(local_day, dict):
        if not market_date or any(
            local_day.get(key) != market_date
            for key in (
                "market_local_date",
                "model_local_date",
                "independent_anchor_local_date",
            )
        ):
            blockers.append("MODEL_AUDIT_LOCAL_DAY_MISMATCH")

    forecast_day = row.get("forecast_valid_local_day_audit")
    if isinstance(forecast_day, dict):
        if (
            not market_date
            or forecast_day.get("market_local_date") != market_date
            or forecast_day.get("model_local_date") != market_date
            or forecast_day.get("derived_forecast_local_date") != market_date
            or not forecast_day.get("station_timezone")
            or _aware_iso(forecast_day.get("forecast_valid_time")) is None
        ):
            blockers.append("MODEL_AUDIT_FORECAST_VALID_DAY_MISMATCH")

    anchor_day = row.get("anchor_valid_local_day_audit")
    if isinstance(anchor_day, dict):
        if (
            not market_date
            or anchor_day.get("market_local_date") != market_date
            or anchor_day.get("independent_anchor_local_date") != market_date
            or anchor_day.get("derived_anchor_local_date") != market_date
            or not anchor_day.get("station_timezone")
            or _aware_iso(anchor_day.get("independent_anchor_valid_time")) is None
        ):
            blockers.append("MODEL_AUDIT_ANCHOR_VALID_DAY_MISMATCH")

    run_quote = row.get("model_run_quote_time_audit")
    if isinstance(run_quote, dict):
        run_time = _aware_iso(run_quote.get("model_run_time"))
        quote_time = _aware_iso(run_quote.get("quote_observed_at"))
        if run_time is None or quote_time is None or run_time > quote_time:
            blockers.append("MODEL_AUDIT_RUN_QUOTE_TIME_MISMATCH")
        if isinstance(sanity, dict):
            calibration_end = _aware_iso(sanity.get("calibration_window_end"))
            anchor_observed = _aware_iso(sanity.get("independent_anchor_observed_at"))
            if calibration_end is not None and run_time is not None and calibration_end > run_time:
                blockers.append("MODEL_AUDIT_CALIBRATION_LOOKAHEAD_DETECTED")
            if anchor_observed is not None and quote_time is not None and anchor_observed > quote_time:
                blockers.append("MODEL_AUDIT_ANCHOR_LOOKAHEAD_DETECTED")

    blockers = sorted(set(blockers))
    verified = not blockers
    return {
        "model_audit_chain_verified": verified,
        "blockers": blockers,
        "classification": (
            "MODEL_AUDIT_CHAIN_VERIFIED_SHADOW"
            if verified
            else "MODEL_AUDIT_CHAIN_UNVERIFIED_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
