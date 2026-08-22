"""V4 settlement-based capital-lock analysis for weather PAPER probes.

Only v4 settlement events whose provenance digest recomputes from the actual ledger
payload are allowed to reach realized-P/L accounting. Older v2/v3/free-form settlement
IDs are quarantined as right-censored research evidence.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger import parse_and_verify_weather_settlement_external_id
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def analyze_weather_paper_capital_lock_v4(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    hardened: list[LedgerEvent] = []
    verified = 0
    quarantined = 0
    providers: dict[str, int] = {}

    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue

        provenance = parse_and_verify_weather_settlement_external_id(event)
        if provenance is not None:
            verified += 1
            provider = provenance["provider"]
            providers[provider] = providers.get(provider, 0) + 1
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
    result["settlement_provenance_version"] = "RECOMPUTABLE_TERMINAL_PAYLOAD_BOUND_V4"
    result["verified_v4_settlement_events"] = verified
    result["legacy_or_tampered_settlement_events_quarantined"] = quarantined
    result["verified_terminal_providers"] = providers
    result["settlement_validation"] = (
        "V4_DIGEST_RECOMPUTED_FROM_TRADE_GROUP_MARKET_SETTLEMENT_KEY_PROVIDER_SOURCE_HASH_"
        "SIDE_QUANTITY_PAYOUT_SETTLEMENT_TIMESTAMP_PLUS_BASE_EXACT_KEY_BINARY_PAYOUT_FINITE_CHECKS"
    )
    return result
