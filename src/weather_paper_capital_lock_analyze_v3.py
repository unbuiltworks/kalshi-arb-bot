"""V3 settlement-based capital-lock analysis for weather PAPER probes.

Only weather settlement events whose v3 external id can be parsed and recomputed
from the actual ledger event identity are allowed to reach realized-P/L accounting.
V2 digest-shaped ids are quarantined because downstream analysis cannot independently
reconstruct their provider/source binding.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger_v3 import (
    parse_and_verify_weather_settlement_external_id_v3,
)
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def analyze_weather_paper_capital_lock_v3(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    hardened: list[LedgerEvent] = []
    quarantined = 0
    verified = 0
    verified_sources: dict[str, int] = {}

    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue

        provenance = parse_and_verify_weather_settlement_external_id_v3(event)
        if provenance is not None:
            verified += 1
            source_key = f"{provenance['provider']}|{provenance['source_url']}"
            verified_sources[source_key] = verified_sources.get(source_key, 0) + 1
            hardened.append(event)
            continue

        quarantined += 1
        hardened.append(
            LedgerEvent(
                trade_group_id=event.trade_group_id,
                settlement_key=event.settlement_key,
                category=event.category,
                venue=event.venue,
                market_id=event.market_id,
                event_type=event.event_type,
                ts_ms=event.ts_ms,
                side=event.side,
                quantity=event.quantity,
                price=event.price,
                fee=event.fee,
                payout=event.payout,
                external_id=None,
            )
        )

    result = analyze_weather_paper_capital_lock(hardened)
    result["settlement_provenance_version"] = "RECOMPUTABLE_IDENTITY_BOUND_V3"
    result["verified_v3_settlement_events"] = verified
    result["legacy_unbound_or_tampered_settlement_events_quarantined"] = quarantined
    result["verified_terminal_sources"] = verified_sources
    result["settlement_validation"] = (
        "RECOMPUTED_V3_TRADE_GROUP_MARKET_SETTLEMENT_KEY_PROVIDER_SOURCE_BINDING_PLUS_"
        "OFFICIAL_PROVIDER_SOURCE_ALLOWLIST_AND_LEGACY_EXACT_KEY_BINARY_PAYOUT_FINITE_CHECKS"
    )
    return result
