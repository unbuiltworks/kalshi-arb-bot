"""Canonical read-only weather SHADOW row with exact-contract, quote, and side binding.

V2 performs exact-market settlement, model, timing, fee, and provenance audits, but its
legacy output schema can retain ``best_shadow_side`` as a full side-economics object and
can leave top-level fee/EV fields bound to a different side. Shared portfolio reporting
must never infer executable economics from that ambiguous handoff.

This wrapper additionally requires raw Kalshi Get Market evidence for the exact sibling
contract and raw exact-orderbook evidence for executable price/depth/freshness. Settlement
certification cannot survive solely on event-level source provenance or producer-supplied
bracket booleans, and EV certification cannot survive producer-supplied book fields alone.

No order-entry clients exist here; this producer is permanently read-only/live-disabled.
"""
from __future__ import annotations

from typing import Any

from src.weather_exact_market_contract_guard import audit_exact_weather_market_contract
from src.weather_exact_quote_guard import audit_exact_weather_quote
from src.weather_selected_side_schema_guard import normalize_selected_side_schema
from src.weather_settlement_certified_v2 import (
    build_exact_market_certified_shadow_row as build_v2_shadow_row,
)


def _withhold_uncertified_economics(row: dict[str, Any], status: str) -> None:
    row["ev_certified_shadow"] = False
    row["best_shadow_side"] = None
    row["best_shadow_side_detail"] = None
    row["modeled_taker_fee_cents"] = None
    row["modeled_after_fee_ev_cents"] = None
    row["modeled_after_fee_edge_cents_per_contract"] = None
    row["forecast_bracket_probability"] = None
    row["economics_status"] = status
    for side in row.get("side_economics") or []:
        side["forecast_side_probability"] = None
        side["modeled_after_fee_ev_cents"] = None
        side["modeled_after_fee_edge_cents_per_contract"] = None


def _withhold_uncertified_contract_economics(row: dict[str, Any]) -> None:
    """Fail closed when exact sibling-contract evidence is absent or inconsistent."""
    row["settlement_certified_shadow"] = False
    _withhold_uncertified_economics(row, "WITHHELD_EXACT_MARKET_CONTRACT_UNVERIFIED")


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
    exact_market_api_request_url: str | None = None,
    exact_market_api_market: dict[str, Any] | None = None,
    exact_orderbook_api_request_url: str | None = None,
    exact_orderbook_api_response: dict[str, Any] | None = None,
    orderbook_observed_at: str | None = None,
    audit_observed_at: str | None = None,
) -> dict[str, Any]:
    """Return one canonical weather SHADOW row with exact contract/book/side binding.

    Raw ``GET /markets/{ticker}`` evidence is mandatory for settlement certification.
    Raw ``GET /markets/{ticker}/orderbook`` evidence plus independent timestamps is
    mandatory for EV certification. Missing or mismatched evidence fails closed.
    """
    row = build_v2_shadow_row(
        series=series,
        market=market,
        quote=quote,
        station=station,
        forecast=forecast,
        fee_changes=fee_changes,
        fee_history_complete=fee_history_complete,
        max_quote_age_ms=max_quote_age_ms,
    )

    contract_audit = audit_exact_weather_market_contract(
        target_market_ticker=market.get("ticker"),
        target_event_ticker=market.get("event_ticker"),
        api_request_url=exact_market_api_request_url,
        api_market=exact_market_api_market,
    )
    row["exact_market_contract_audit"] = contract_audit
    row["exact_market_contract_verified"] = (
        contract_audit["exact_market_contract_verified"] is True
    )

    if row["exact_market_contract_verified"] is not True:
        blockers = set(row.get("blockers") or [])
        blockers.update(contract_audit.get("blockers") or [])
        blockers.add("EXACT_MARKET_CONTRACT_AUDIT_REQUIRED")
        row["blockers"] = sorted(blockers)
        _withhold_uncertified_contract_economics(row)

    quote_audit = audit_exact_weather_quote(
        target_market_ticker=market.get("ticker"),
        quote=quote,
        api_request_url=exact_orderbook_api_request_url,
        api_orderbook_response=exact_orderbook_api_response,
        orderbook_observed_at=orderbook_observed_at,
        audit_observed_at=audit_observed_at,
        max_quote_age_ms=max_quote_age_ms,
    )
    row["exact_quote_audit"] = quote_audit
    row["exact_quote_verified"] = quote_audit["exact_quote_verified"] is True
    row["computed_quote_age_ms"] = quote_audit.get("computed_quote_age_ms")

    if row["exact_quote_verified"] is not True:
        blockers = set(row.get("blockers") or [])
        blockers.update(quote_audit.get("blockers") or [])
        blockers.add("EXACT_MARKET_ORDERBOOK_AUDIT_REQUIRED")
        row["blockers"] = sorted(blockers)
        _withhold_uncertified_economics(row, "WITHHELD_EXACT_MARKET_ORDERBOOK_UNVERIFIED")

    row = normalize_selected_side_schema(row)

    # Defense in depth: this producer is research-only even if upstream schemas evolve.
    row["read_only"] = True
    row["order_entry"] = False
    row["live_probe_ready"] = False
    if (
        row.get("selected_side_schema_verified") is not True
        or row.get("exact_market_contract_verified") is not True
        or row.get("exact_quote_verified") is not True
    ):
        row["ev_certified_shadow"] = False
    if row.get("exact_market_contract_verified") is not True:
        row["settlement_certified_shadow"] = False
    return row