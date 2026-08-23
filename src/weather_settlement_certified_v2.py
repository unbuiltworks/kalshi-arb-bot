"""Fail-closed weather SHADOW certification with exact-market settlement provenance.

This wrapper composes the existing read-only weather certification pipeline with the
hardened exact-market source auditor, independent model-sanity audit, scored-model
provider-provenance audit, explicit local-day binding audit, forecast valid-time ->
station-local-day audit, and independent-anchor valid-time -> station-local-day audit.
It intentionally requires explicit source provenance extracted for the exact
contract/date and never treats the current Series settlement source as higher authority
than verified exact-market evidence.

Settlement certification and EV/model certification remain deliberately separate. A
contract can have fully audited settlement provenance while forecast-model economics are
withheld because the model is not calibrated, not independently anchored to the same
station and exact contract local day, disagrees with that anchor beyond an audited
tolerance, does not declare enough provider/component provenance to prove that the
anchor is actually independent, or either the model/anchor actual valid timestamp does
not map to the exact contract day in the settlement station's audited timezone.

No order-entry clients, credentials, live-arm state, or execution paths exist here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.weather_anchor_valid_local_day_guard import audit_anchor_valid_local_day
from src.weather_exact_market_source_audit import audit_exact_market_source
from src.weather_forecast_valid_local_day_guard import audit_forecast_valid_local_day
from src.weather_local_day_binding_guard import audit_weather_local_day_binding
from src.weather_model_provider_provenance_guard import audit_model_provider_provenance
from src.weather_model_sanity_guard import audit_weather_model_sanity
from src.weather_settlement_certified import build_shadow_row


_LEGACY_SERIES_REGIME_BLOCKERS = {
    "EXACT_MARKET_IDENTITY_UNVERIFIED",
    "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED",
}


def _withhold_model_economics(row: dict[str, Any]) -> None:
    """Retain book/depth/fee diagnostics while removing uncertified model EV."""
    row["best_shadow_side"] = None
    row["forecast_bracket_probability"] = None
    row["modeled_after_fee_ev_cents"] = None
    for side in row.get("side_economics") or []:
        side["forecast_side_probability"] = None
        side["modeled_after_fee_ev_cents"] = None
        side["modeled_after_fee_edge_cents_per_contract"] = None


def _model_sanity_audit(
    *,
    forecast: dict[str, Any] | None,
    station: dict[str, Any],
    quote: dict[str, Any],
) -> dict[str, Any]:
    forecast = forecast or {}
    station_id = forecast.get("model_station_id")
    model_id = forecast.get("model_id") or forecast.get("model")
    audit = audit_weather_model_sanity(
        model_mean_f=forecast.get("model_mean_f"),
        model_station_id=station_id,
        independent_anchor_high_f=forecast.get("independent_anchor_high_f"),
        independent_anchor_station_id=forecast.get("independent_anchor_station_id"),
        independent_anchor_verified=forecast.get("independent_anchor_verified"),
        same_local_day_verified=forecast.get("same_local_day_verified"),
        disagreement_tolerance_f=forecast.get("disagreement_tolerance_f"),
        disagreement_tolerance_audited=forecast.get("disagreement_tolerance_audited"),
        model_calibration_verified=forecast.get("model_calibration_verified"),
        model_id=model_id,
        model_provider=forecast.get("model_provider"),
        model_component_providers=forecast.get("model_component_providers"),
        calibration_model_id=forecast.get("calibration_model_id"),
        calibration_target=forecast.get("calibration_target"),
        calibration_evidence_verified=forecast.get("calibration_evidence_verified"),
        calibration_evidence_id=forecast.get("calibration_evidence_id"),
        calibration_sample_count=forecast.get("calibration_sample_count"),
        calibration_station_id=forecast.get("calibration_station_id"),
        calibration_window_end=forecast.get("calibration_window_end"),
        model_run_time=forecast.get("model_run_time"),
        quote_observed_at=quote.get("observed_at"),
        independent_anchor_source_id=forecast.get("independent_anchor_source_id"),
        independent_anchor_provider=forecast.get("independent_anchor_provider"),
        independent_anchor_model_id=forecast.get("independent_anchor_model_id"),
        independent_anchor_provenance_url=forecast.get("independent_anchor_provenance_url"),
        independent_anchor_observed_at=forecast.get("independent_anchor_observed_at"),
    )

    settlement_station = str(
        station.get("station_id") or station.get("cli_product") or ""
    ).strip().upper()
    model_station = str(station_id or "").strip().upper()
    audit = deepcopy(audit)
    if not settlement_station or not model_station or model_station != settlement_station:
        blockers = set(audit.get("blockers") or [])
        blockers.add("FORECAST_SETTLEMENT_STATION_MISMATCH")
        audit["blockers"] = sorted(blockers)
        audit["model_sanity_verified"] = False
        audit["classification"] = "MODEL_DISAGREEMENT_RESEARCH_ONLY"
        audit["settlement_station_id"] = settlement_station or None
    else:
        audit["settlement_station_id"] = settlement_station
    return audit


def _model_provider_audit(forecast: dict[str, Any] | None) -> dict[str, Any]:
    forecast = forecast or {}
    return audit_model_provider_provenance(
        model_provider=forecast.get("model_provider"),
        model_component_providers=forecast.get("model_component_providers"),
        independent_anchor_provider=forecast.get("independent_anchor_provider"),
    )


def _local_day_audit(
    *,
    market: dict[str, Any],
    forecast: dict[str, Any] | None,
) -> dict[str, Any]:
    forecast = forecast or {}
    return audit_weather_local_day_binding(
        market_date=market.get("market_date"),
        model_local_date=forecast.get("model_local_date"),
        independent_anchor_local_date=forecast.get("independent_anchor_local_date"),
        same_local_day_verified=forecast.get("same_local_day_verified"),
    )


def _forecast_valid_local_day_audit(
    *,
    market: dict[str, Any],
    forecast: dict[str, Any] | None,
    station: dict[str, Any],
) -> dict[str, Any]:
    forecast = forecast or {}
    return audit_forecast_valid_local_day(
        market_date=market.get("market_date"),
        model_local_date=forecast.get("model_local_date"),
        forecast_valid_time=forecast.get("forecast_valid_time"),
        station_timezone=station.get("timezone"),
        timezone_mapping_verified=station.get("timezone_mapping_verified"),
    )


def _anchor_valid_local_day_audit(
    *,
    market: dict[str, Any],
    forecast: dict[str, Any] | None,
    station: dict[str, Any],
) -> dict[str, Any]:
    forecast = forecast or {}
    return audit_anchor_valid_local_day(
        market_date=market.get("market_date"),
        independent_anchor_local_date=forecast.get("independent_anchor_local_date"),
        independent_anchor_valid_time=forecast.get("independent_anchor_valid_time"),
        station_timezone=station.get("timezone"),
        timezone_mapping_verified=station.get("timezone_mapping_verified"),
    )


def build_exact_market_certified_shadow_row(
    *,
    series: dict[str, Any],
    market: dict[str, Any],
    quote: dict[str, Any],
    station: dict[str, Any],
    forecast: dict[str, Any] | None = None,
    fee_changes: list[dict[str, Any]] | None = None,
    fee_history_complete: bool = False,
    max_quote_age_ms: int = 2_000,
) -> dict[str, Any]:
    """Build a weather row only after exact-contract settlement provenance verifies.

    Model EV additionally requires explicit forecast-side station and local-day evidence.
    ``model_local_date`` and ``independent_anchor_local_date`` must be canonical ISO dates
    and both must equal the exact market ``market_date``. In addition, the timezone-aware
    ``forecast_valid_time`` and ``independent_anchor_valid_time`` must each independently
    resolve to that same market day using the settlement station's explicitly audited
    IANA timezone. Producer-supplied date flags are never sufficient by themselves.
    """
    base = build_shadow_row(
        series=series,
        market=market,
        quote=quote,
        station=station,
        forecast=forecast,
        fee_changes=fee_changes,
        fee_history_complete=fee_history_complete,
        max_quote_age_ms=max_quote_age_ms,
    )

    audit = audit_exact_market_source(
        series_ticker=series.get("ticker"),
        event_ticker=market.get("event_ticker"),
        market_ticker=market.get("ticker"),
        market_date=market.get("market_date"),
        provider=market.get("settlement_source_provider"),
        source_url=market.get("settlement_source_url"),
        evidence_url=market.get("settlement_source_evidence_url"),
        evidence_market_ticker=market.get("settlement_source_evidence_market_ticker"),
        evidence_market_date=market.get("settlement_source_evidence_market_date"),
    )
    model_sanity = _model_sanity_audit(forecast=forecast, station=station, quote=quote)
    provider_provenance = _model_provider_audit(forecast)
    local_day_binding = _local_day_audit(market=market, forecast=forecast)
    valid_time_local_day = _forecast_valid_local_day_audit(
        market=market,
        forecast=forecast,
        station=station,
    )
    anchor_valid_time_local_day = _anchor_valid_local_day_audit(
        market=market,
        forecast=forecast,
        station=station,
    )

    row = deepcopy(base)
    row["exact_market_source_audit"] = audit
    row["exact_market_source_verified"] = audit["verified"] is True
    row["model_sanity_audit"] = model_sanity
    row["model_provider_provenance_audit"] = provider_provenance
    row["model_provider_provenance_verified"] = (
        provider_provenance["provider_provenance_verified"] is True
    )
    row["local_day_binding_audit"] = local_day_binding
    row["local_day_binding_verified"] = (
        local_day_binding["local_day_binding_verified"] is True
    )
    row["forecast_valid_local_day_audit"] = valid_time_local_day
    row["forecast_valid_local_day_verified"] = (
        valid_time_local_day["forecast_valid_local_day_verified"] is True
    )
    row["anchor_valid_local_day_audit"] = anchor_valid_time_local_day
    row["anchor_valid_local_day_verified"] = (
        anchor_valid_time_local_day["anchor_valid_local_day_verified"] is True
    )
    row["model_sanity_verified"] = (
        model_sanity["model_sanity_verified"] is True
        and row["model_provider_provenance_verified"] is True
        and row["local_day_binding_verified"] is True
        and row["forecast_valid_local_day_verified"] is True
        and row["anchor_valid_local_day_verified"] is True
    )
    row["read_only"] = True
    row["order_entry"] = False
    row["live_probe_ready"] = False

    if audit["verified"] is not True:
        blockers = set(row.get("blockers") or [])
        blockers.update(audit.get("blockers") or [])
        blockers.add("EXACT_MARKET_SOURCE_AUDIT_REQUIRED")
        row["blockers"] = sorted(blockers)
        row["settlement_certified_shadow"] = False
        row["ev_certified_shadow"] = False
        row["side_economics"] = []
        row["modeled_taker_fee_cents"] = None
        _withhold_model_economics(row)
        row["economics_status"] = "WITHHELD_EXACT_MARKET_SOURCE_UNVERIFIED"
        return row

    blockers = {
        blocker
        for blocker in (row.get("blockers") or [])
        if blocker not in _LEGACY_SERIES_REGIME_BLOCKERS
    }
    row["blockers"] = sorted(blockers)

    settlement_certified = len(blockers) == 0
    forecast_blockers = list(row.get("forecast_blockers") or [])
    model_sanity_verified = row["model_sanity_verified"] is True
    if not model_sanity_verified:
        _withhold_model_economics(row)

    best_side = row.get("best_shadow_side")
    ev_certified = (
        settlement_certified
        and not forecast_blockers
        and model_sanity_verified
        and best_side is not None
    )
    row["settlement_certified_shadow"] = settlement_certified
    row["ev_certified_shadow"] = ev_certified

    if ev_certified:
        row["economics_status"] = "EXACT_MARKET_MODEL_SANITY_AND_EV_CERTIFIED_SHADOW"
    elif settlement_certified and not row["local_day_binding_verified"]:
        row["economics_status"] = "LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
    elif settlement_certified and not row["forecast_valid_local_day_verified"]:
        row["economics_status"] = "FORECAST_VALID_TIME_LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
    elif settlement_certified and not row["anchor_valid_local_day_verified"]:
        row["economics_status"] = "INDEPENDENT_ANCHOR_VALID_TIME_LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
    elif settlement_certified and not row["model_provider_provenance_verified"]:
        row["economics_status"] = "MODEL_PROVIDER_PROVENANCE_UNVERIFIED_RESEARCH_ONLY"
    elif settlement_certified and not model_sanity_verified:
        row["economics_status"] = "MODEL_SANITY_UNVERIFIED_RESEARCH_ONLY"
    else:
        row["economics_status"] = "EXACT_MARKET_CERTIFIED_EV_NOT_CERTIFIED"
    return row
