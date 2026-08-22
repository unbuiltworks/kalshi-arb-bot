"""V2 settlement-based capital-lock analysis for weather PAPER probes.

This wrapper tightens the legacy analyzer's settlement provenance requirement. Only
settlement events emitted by ``weather_certified_settlement_ledger`` are allowed to
reach realized-P/L accounting. Legacy/free-form certified prefixes are treated as
unaudited and therefore remain right-censored.

Research-only; no venue client or order-entry path exists here.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger import PREFIX
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def _is_identity_bound_weather_settlement(event: LedgerEvent) -> bool:
    return (
        event.category == "weather"
        and event.venue == "kalshi"
        and event.event_type == "settlement"
        and isinstance(event.external_id, str)
        and event.external_id.startswith(PREFIX)
        and len(event.external_id) == len(PREFIX) + 64
        and all(ch in "0123456789abcdef" for ch in event.external_id[len(PREFIX):])
    )


def analyze_weather_paper_capital_lock_v2(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    """Analyze capital lock while failing closed on legacy settlement provenance.

    Non-settlement events pass through unchanged. Any settlement that is not in the
    identity-bound v2 namespace has its external id cleared before reaching the legacy
    analyzer, which causes that analyzer to classify it as unaudited/right-censored.
    We deliberately do not invent or upgrade provenance here.
    """
    hardened: list[LedgerEvent] = []
    legacy_or_unbound_settlements = 0
    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue
        if _is_identity_bound_weather_settlement(event):
            # The legacy analyzer checks for the old prefix. V2 is deliberately more
            # specific but retains that prefix, so the event can pass unchanged.
            hardened.append(event)
            continue
        legacy_or_unbound_settlements += 1
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
    result["settlement_provenance_version"] = "IDENTITY_BOUND_V2"
    result["legacy_or_unbound_settlement_events_quarantined"] = legacy_or_unbound_settlements
    result["settlement_validation"] = (
        "IDENTITY_BOUND_V2_CERTIFIED_RECONCILER_EVENT_PLUS_"
        "EXACT_KEY_CATEGORY_MARKET_OPTIONAL_SIDE_BINARY_TERMINAL_PAYOUT_AND_FINITE_DECIMALS"
    )
    return result
