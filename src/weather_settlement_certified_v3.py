"""Canonical read-only weather SHADOW row with selected-side economics binding.

V2 performs exact-market settlement, model, timing, fee, and provenance audits, but its
legacy output schema can retain ``best_shadow_side`` as a full side-economics object and
can leave top-level fee/EV fields bound to a different side.  Shared portfolio reporting
must never infer executable economics from that ambiguous handoff.

This wrapper applies the fail-closed selected-side schema guard to every v2 row.  It has
no order-entry clients and permanently preserves read-only/live-disabled state.
"""
from __future__ import annotations

from typing import Any

from src.weather_selected_side_schema_guard import normalize_selected_side_schema
from src.weather_settlement_certified_v2 import (
    build_exact_market_certified_shadow_row as build_v2_shadow_row,
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
    """Return one canonical weather SHADOW row with exact selected-side binding."""
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
    row = normalize_selected_side_schema(row)

    # Defense in depth: this producer is research-only even if upstream schemas evolve.
    row["read_only"] = True
    row["order_entry"] = False
    row["live_probe_ready"] = False
    if row.get("selected_side_schema_verified") is not True:
        row["ev_certified_shadow"] = False
    return row
