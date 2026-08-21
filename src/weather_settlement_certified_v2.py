"""Fail-closed weather SHADOW certification with exact-market settlement provenance.

This wrapper composes the existing read-only weather certification pipeline with the
hardened exact-market source auditor.  It intentionally requires explicit source
provenance extracted for the exact contract/date and never falls back to the current
Series settlement source.  This prevents historical contracts around settlement-source
transitions from inheriting a later recurring-Series regime.

No order-entry clients, credentials, live-arm state, or execution paths exist here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.weather_exact_market_source_audit import audit_exact_market_source
from src.weather_settlement_certified import build_shadow_row


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

    Required exact-market provenance fields on ``market``:
      - settlement_source_provider
      - settlement_source_url
      - settlement_source_evidence_url
      - settlement_source_evidence_market_ticker
      - settlement_source_evidence_market_date

    Missing or mismatched provenance quarantines all EV/capacity economics while
    retaining diagnostic quote/fee metadata from the underlying read-only adapter.
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

    row = deepcopy(base)
    row["exact_market_source_audit"] = audit
    row["exact_market_source_verified"] = audit["verified"] is True
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
        row["best_shadow_side"] = None
        row["forecast_bracket_probability"] = None
        row["side_economics"] = []
        row["modeled_taker_fee_cents"] = None
        row["modeled_after_fee_ev_cents"] = None
        row["economics_status"] = "WITHHELD_EXACT_MARKET_SOURCE_UNVERIFIED"
        return row

    # Exact-market audit is an additional gate, never an upgrade by itself.  The
    # underlying adapter must still independently pass station/day/bracket, fee,
    # quote-freshness and forecast provenance checks.
    row["economics_status"] = (
        "EXACT_MARKET_AND_EV_CERTIFIED_SHADOW"
        if row.get("ev_certified_shadow") is True
        else "EXACT_MARKET_CERTIFIED_EV_NOT_CERTIFIED"
    )
    return row
