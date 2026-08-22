"""Compatibility wrapper for hardened weather PAPER capital-lock accounting.

The historical v2 entrypoint now accepts only the current source-verifiable v5
settlement provenance emitted by ``weather_certified_settlement_ledger``. Older or
free-form settlement IDs are quarantined as right-censored research evidence.
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
    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue
        if parse_and_verify_weather_settlement_external_id(event) is not None:
            hardened.append(event)
            continue
        quarantined += 1
        hardened.append(_quarantine_settlement(event))

    result = analyze_weather_paper_capital_lock(hardened)
    result["settlement_provenance_version"] = "SOURCE_AND_TERMINAL_PAYLOAD_BOUND_V5"
    result["legacy_or_unbound_settlement_events_quarantined"] = quarantined
    result["settlement_validation"] = (
        "V5_OFFICIAL_SOURCE_URL_RECHECK_PLUS_DIGEST_RECOMPUTED_FROM_TRADE_GROUP_MARKET_"
        "SETTLEMENT_KEY_PROVIDER_SOURCE_SIDE_QUANTITY_PAYOUT_SETTLEMENT_TIMESTAMP_PLUS_"
        "EXACT_KEY_CATEGORY_MARKET_BINARY_TERMINAL_PAYOUT_AND_FINITE_DECIMALS"
    )
    return result
