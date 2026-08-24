"""Read-only weather certification dimension audit.

The canonical weather row intentionally fails closed, but its aggregate blocker list can
mix settlement/rule provenance with quote, fee, and model readiness. This helper keeps
those dimensions separate for portfolio/reporting use without changing any upstream
readiness state.

A critical invariant here is that *absence of a blocker is not affirmative evidence*.
Each dimension requires explicit, literal verification evidence in addition to having no
classified blockers. It never authorizes order entry or live probes.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.weather_blocker_telemetry_guard import audit_weather_blocker_telemetry
from src.weather_exact_market_source_audit import audit_exact_market_source
from src.weather_fee_resolution_binding_guard import audit_weather_fee_resolution_binding
from src.weather_quote_freshness_binding_guard import audit_weather_quote_freshness_binding

SETTLEMENT_BLOCKERS = {
    "RULES_PENDING", "EXACT_MARKET_IDENTITY_UNVERIFIED", "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED",
    "EXACT_MARKET_SOURCE_AUDIT_REQUIRED", "EXACT_MARKET_EVIDENCE_MISMATCH", "EXACT_MARKET_EVIDENCE_UNOFFICIAL",
    "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT", "EXACT_MARKET_SETTLEMENT_SOURCE_UNVERIFIED",
    "STATION_IDENTITY_UNVERIFIED", "LOCAL_DAY_UNVERIFIED", "BRACKET_SEMANTICS_UNVERIFIED",
    "SETTLEMENT_SOURCE_UNVERIFIED", "TWC_SOURCE_ACCESS_REQUIRED",
}
MARKET_DATA_BLOCKERS = {"BOOK_UNVERIFIED", "QUOTE_STALE"}
FEE_BLOCKERS = {"FEE_UNVERIFIED", "FEE_SCHEDULE_UNVERIFIED", "FEE_HISTORY_UNVERIFIED", "AUTHORITATIVE_FEE_AUDIT_REQUIRED", "EXACT_NET_FEE_ROUNDING_UNVERIFIED"}
MODEL_BLOCKER_PREFIXES = ("FORECAST_", "MODEL_", "INDEPENDENT_ANCHOR_")
MODEL_BLOCKERS = {"LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY", "MODEL_SANITY_AUDIT_REQUIRED", "MODEL_RESEARCH_ONLY"}
_CANONICAL_FEE_SOURCES = {"series_current", "scheduled_fee_change", "historical_fee_change"}


def _finite_nonnegative(value: Any) -> bool:
    try: parsed = Decimal(str(value))
    except Exception: return False
    return parsed.is_finite() and parsed >= 0


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool): return None
    try: parsed = Decimal(str(value))
    except Exception: return None
    return parsed if parsed.is_finite() else None


def _aware_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip(): return None
    text = value.strip()
    if text.endswith("Z"): text = text[:-1] + "+00:00"
    try: parsed = datetime.fromisoformat(text)
    except ValueError: return None
    if parsed.tzinfo is None or parsed.utcoffset() is None: return None
    return parsed


def _valid_executable_side(row: dict[str, Any], side: str) -> bool:
    price_fp = _decimal(row.get(f"{side}_ask_dollars")); qty_fp = _decimal(row.get(f"{side}_ask_qty_fp"))
    if price_fp is not None and qty_fp is not None:
        return Decimal("0") < price_fp < Decimal("1") and qty_fp >= Decimal("1")
    cents = row.get(f"{side}_ask_cents"); qty = row.get(f"{side}_available_contracts")
    return isinstance(cents, int) and not isinstance(cents, bool) and 1 <= cents <= 99 and isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1


def _book_evidence_verified(row: dict[str, Any]) -> bool:
    freshness = audit_weather_quote_freshness_binding(row)
    return freshness.get("quote_freshness_binding_verified") is True and any(_valid_executable_side(row, side) for side in ("yes", "no"))


def _fee_evidence_verified(row: dict[str, Any]) -> bool:
    multiplier = row.get("fee_multiplier"); series = str(row.get("series_ticker") or "").strip().upper(); fee_series = str(row.get("fee_series_ticker") or "").strip().upper()
    source = str(row.get("fee_metadata_source") or "").strip(); quote = _aware_iso(row.get("quote_observed_at")); observed = _aware_iso(row.get("fee_schedule_observed_at"))
    basic = row.get("fee_type") == "quadratic" and _finite_nonnegative(multiplier) and source in _CANONICAL_FEE_SOURCES and row.get("fee_schedule_checked") is True and row.get("fee_schedule_feed_complete") is True and bool(series) and fee_series == series and quote is not None and observed is not None and observed >= quote
    return basic and audit_weather_fee_resolution_binding(row).get("fee_resolution_binding_verified") is True


def _model_evidence_verified(row: dict[str, Any]) -> bool:
    try:
        p = Decimal(str(row.get("forecast_bracket_probability"))); probability_ok = p.is_finite() and Decimal(0) <= p <= Decimal(1)
    except Exception: probability_ok = False
    return probability_ok and all(row.get(k) is True for k in ("model_sanity_verified", "model_provider_provenance_verified", "local_day_binding_verified", "forecast_valid_local_day_verified", "anchor_valid_local_day_verified", "model_run_quote_time_verified"))


def _settlement_identity_semantics_verified(row: dict[str, Any]) -> bool:
    """Require affirmative exact-contract identity semantics, not just no blockers.

    The source audit proves which official terminal source applies to the exact market.
    It does not by itself prove the producer independently verified the station mapping,
    local-day definition, and exact bracket/tail semantics used by that contract. These
    fields therefore must survive as literal booleans into the canonical report.
    """
    return all(
        row.get(key) is True
        for key in (
            "station_identity_verified",
            "local_day_verified",
            "bracket_semantics_verified",
        )
    )


def _exact_market_source_audit(row: dict[str, Any]) -> dict[str, Any]:
    """Re-run exact-market settlement provenance instead of trusting producer booleans."""
    return audit_exact_market_source(
        series_ticker=row.get("series_ticker"),
        event_ticker=row.get("event_ticker"),
        market_ticker=row.get("market_ticker"),
        market_date=row.get("market_date"),
        provider=row.get("settlement_source_provider"),
        source_url=row.get("settlement_source_url"),
        evidence_url=row.get("exact_market_evidence_url"),
        evidence_market_ticker=row.get("evidence_market_ticker"),
        evidence_market_date=row.get("evidence_market_date"),
    )


def audit_weather_certification_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict): raise TypeError("row must be a dict")

    blocker_audit = audit_weather_blocker_telemetry(row)
    blockers = set(blocker_audit.get("blockers") or [])
    telemetry_verified = blocker_audit.get("blocker_telemetry_verified") is True
    settlement = sorted(blockers & SETTLEMENT_BLOCKERS); market_data = sorted(blockers & MARKET_DATA_BLOCKERS); fee = sorted(blockers & FEE_BLOCKERS)
    model = sorted(b for b in blockers if b in MODEL_BLOCKERS or b.startswith(MODEL_BLOCKER_PREFIXES))
    classified = set(settlement) | set(market_data) | set(fee) | set(model)
    unclassified = sorted(blockers - classified)

    exact_market_source = _exact_market_source_audit(row)
    if exact_market_source.get("verified") is not True:
        settlement.extend(exact_market_source.get("blockers") or ["EXACT_MARKET_SOURCE_AUDIT_REQUIRED"])
    if row.get("exact_market_source_verified") is not True: settlement.append("EXACT_MARKET_SOURCE_AUDIT_REQUIRED")
    identity_semantics_verified = _settlement_identity_semantics_verified(row)
    if row.get("station_identity_verified") is not True: settlement.append("STATION_IDENTITY_EVIDENCE_REQUIRED")
    if row.get("local_day_verified") is not True: settlement.append("LOCAL_DAY_EVIDENCE_REQUIRED")
    if row.get("bracket_semantics_verified") is not True: settlement.append("BRACKET_SEMANTICS_EVIDENCE_REQUIRED")
    if row.get("settlement_certified_shadow") is not True: settlement.append("SETTLEMENT_CERTIFICATION_EVIDENCE_REQUIRED")
    settlement = sorted(set(settlement))

    freshness = audit_weather_quote_freshness_binding(row)
    if freshness.get("quote_freshness_binding_verified") is not True: market_data.extend(freshness.get("quote_freshness_binding_blockers") or [])
    if not _book_evidence_verified(row): market_data.append("EXECUTABLE_BOOK_EVIDENCE_REQUIRED")
    market_data = sorted(set(market_data))

    fee_binding = audit_weather_fee_resolution_binding(row)
    if fee_binding.get("fee_resolution_binding_verified") is not True: fee.extend(fee_binding.get("fee_resolution_binding_blockers") or [])
    if not _fee_evidence_verified(row): fee.append("AUTHORITATIVE_FEE_EVIDENCE_REQUIRED")
    fee = sorted(set(fee))
    if not _model_evidence_verified(row): model.append("MODEL_AUDIT_CHAIN_EVIDENCE_REQUIRED")
    model = sorted(set(model))

    settlement_ok = not settlement; market_ok = not market_data; fee_ok = not fee; model_ok = not model
    no_unknowns = telemetry_verified and not unclassified and "MALFORMED_BLOCKER_TELEMETRY" not in blockers
    executable = settlement_ok and market_ok and fee_ok and model_ok and no_unknowns and row.get("ev_certified_shadow") is True and row.get("read_only") is True and row.get("order_entry") is False and row.get("live_probe_ready") is False
    return {
        "category": "weather", "classification": "SHADOW", "blocker_telemetry_verified": telemetry_verified,
        "blocker_source": blocker_audit.get("blocker_source"), "settlement_rules_provenance_verified": settlement_ok,
        "exact_market_source_audit_verified": exact_market_source.get("verified") is True,
        "settlement_identity_semantics_verified": identity_semantics_verified,
        "market_data_verified": market_ok, "quote_freshness_binding_verified": freshness.get("quote_freshness_binding_verified") is True,
        "derived_quote_age_ms": freshness.get("derived_quote_age_ms"), "fee_economics_verified": fee_ok,
        "fee_resolution_binding_verified": fee_binding.get("fee_resolution_binding_verified") is True, "model_probability_verified": model_ok,
        "executable_shadow_prereqs_verified": executable, "settlement_blockers": settlement, "market_data_blockers": market_data,
        "fee_blockers": fee, "model_blockers": model, "unclassified_blockers": unclassified,
        "read_only": True, "order_entry": False, "live_probe_ready": False,
    }
