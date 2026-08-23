"""Compatibility wrapper for hardened weather PAPER capital-lock accounting.

The historical v2 entrypoint now accepts only the current source-and-final-stage
verifiable v6 settlement provenance emitted by ``weather_certified_settlement_ledger``.
Older or free-form settlement IDs are quarantined as right-censored research evidence.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger import parse_and_verify_weather_settlement_external_id
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def _quarantine_settlement(event: LedgerEvent) -> LedgerEvent:
    return LedgerEvent(
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


def analyze_weather_paper_capital_lock_v2(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    hardened: list[LedgerEvent] = []
    quarantined = 0
    stages: dict[str, int] = {}
    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue
        provenance = parse_and_verify_weather_settlement_external_id(event)
        if provenance is not None:
            stage = provenance["terminal_record_stage"]
            stages[stage] = stages.get(stage, 0) + 1
            hardened.append(event)
            continue
        quarantined += 1
        hardened.append(_quarantine_settlement(event))

    result = analyze_weather_paper_capital_lock(hardened)
    result["settlement_provenance_version"] = "SOURCE_FINAL_STAGE_AND_TERMINAL_PAYLOAD_BOUND_V6"
    result["legacy_or_unbound_settlement_events_quarantined"] = quarantined
    result["verified_terminal_record_stages"] = stages
    result["settlement_validation"] = (
        "V6_OFFICIAL_SOURCE_URL_PLUS_FINAL_STAGE_RECHECK_PLUS_DIGEST_RECOMPUTED_FROM_"
        "TRADE_GROUP_MARKET_SETTLEMENT_KEY_PROVIDER_SOURCE_STAGE_SIDE_QUANTITY_PAYOUT_"
        "SETTLEMENT_TIMESTAMP_PLUS_EXACT_KEY_CATEGORY_MARKET_BINARY_TERMINAL_PAYOUT_"
        "AND_FINITE_DECIMALS"
    )
    return result
