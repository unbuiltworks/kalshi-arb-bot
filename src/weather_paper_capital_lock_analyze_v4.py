"""Source-verifiable settlement-based capital-lock analysis for weather PAPER probes.

Despite the historical filename, this wrapper now accepts only current v5 settlement
IDs. V5 embeds the audited terminal source URL so downstream analysis can independently
re-check provider/source authority and recompute the terminal-payload digest. Older
v2/v3/v4/free-form IDs are quarantined as right-censored research evidence.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger import parse_and_verify_weather_settlement_external_id
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def analyze_weather_paper_capital_lock_v5(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    hardened: list[LedgerEvent] = []
    verified = 0
    quarantined = 0
    sources: dict[str, int] = {}

    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue
        provenance = parse_and_verify_weather_settlement_external_id(event)
        if provenance is not None:
            verified += 1
            key = f"{provenance['provider']}|{provenance['source_url']}"
            sources[key] = sources.get(key, 0) + 1
            hardened.append(event)
            continue
        quarantined += 1
        hardened.append(LedgerEvent(
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
        ))

    result = analyze_weather_paper_capital_lock(hardened)
    result["settlement_provenance_version"] = "SOURCE_AND_TERMINAL_PAYLOAD_BOUND_V5"
    result["verified_v5_settlement_events"] = verified
    result["legacy_or_tampered_settlement_events_quarantined"] = quarantined
    result["verified_terminal_sources"] = sources
    result["settlement_validation"] = (
        "V5_OFFICIAL_SOURCE_URL_RECHECK_PLUS_DIGEST_RECOMPUTED_FROM_TRADE_GROUP_MARKET_"
        "SETTLEMENT_KEY_PROVIDER_SOURCE_SIDE_QUANTITY_PAYOUT_SETTLEMENT_TIMESTAMP_PLUS_"
        "BASE_EXACT_KEY_BINARY_PAYOUT_FINITE_CHECKS"
    )
    return result


# Compatibility name for the just-added research wrapper; semantics are v5 fail-closed.
def analyze_weather_paper_capital_lock_v4(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    return analyze_weather_paper_capital_lock_v5(events)
