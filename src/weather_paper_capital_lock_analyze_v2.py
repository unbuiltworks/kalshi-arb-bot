"""Settlement-based capital-lock analysis for weather PAPER probes.

This wrapper tightens the legacy analyzer's settlement provenance requirement. Only
settlement events emitted in the self-verifying v4 namespace by
``weather_certified_settlement_ledger`` are allowed to reach realized-P/L accounting.
Legacy/free-form or merely well-shaped digests are treated as unaudited and remain
right-censored.

Research-only; no venue client or order-entry path exists here.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent
from src.weather_certified_settlement_ledger import PREFIX
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock

_HEX = set("0123456789abcdef")


def _is_hex64(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value)


def _valid_provider_token(value: str) -> bool:
    return bool(value) and all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in value)


def _decimal_token(value: Any) -> str | None:
    if not isinstance(value, Decimal) or not value.is_finite():
        return None
    return format(value.normalize(), "f")


def _is_identity_bound_weather_settlement(event: LedgerEvent) -> bool:
    if not (
        event.category == "weather"
        and event.venue == "kalshi"
        and event.event_type == "settlement"
        and isinstance(event.external_id, str)
        and event.external_id.startswith(PREFIX)
    ):
        return False

    payload = event.external_id[len(PREFIX):]
    parts = payload.split(":")
    if len(parts) != 3:
        return False
    provider, source_hash, claimed_digest = parts
    if not _valid_provider_token(provider) or not _is_hex64(source_hash) or not _is_hex64(claimed_digest):
        return False

    group = str(event.trade_group_id or "").strip()
    market = str(event.market_id or "").strip().upper()
    key = str(event.settlement_key or "").strip()
    side = str(event.side or "").strip().upper()
    quantity_token = _decimal_token(event.quantity)
    payout_token = _decimal_token(event.payout)
    if (
        not group
        or not market
        or not key
        or side not in {"YES", "NO"}
        or quantity_token is None
        or payout_token is None
        or not isinstance(event.ts_ms, int)
        or isinstance(event.ts_ms, bool)
        or event.ts_ms < 0
    ):
        return False

    expected = sha256(
        "\x1f".join(
            (
                group,
                market,
                key,
                provider,
                source_hash,
                side,
                quantity_token,
                payout_token,
                str(event.ts_ms),
            )
        ).encode("utf-8")
    ).hexdigest()
    return claimed_digest == expected


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
    """Analyze capital lock while failing closed on settlement provenance.

    Non-settlement events pass through unchanged. Weather/Kalshi settlements must
    carry a v4 external id whose digest recomputes from the event's exact trade-group,
    market, settlement key, provider token, terminal-source fingerprint, side,
    quantity, payout, and settlement timestamp. Anything else is stripped of
    certified provenance before reaching the legacy analyzer.
    """
    hardened: list[LedgerEvent] = []
    legacy_or_unbound_settlements = 0
    for event in events:
        if event.event_type != "settlement" or event.category != "weather" or event.venue != "kalshi":
            hardened.append(event)
            continue
        if _is_identity_bound_weather_settlement(event):
            hardened.append(event)
            continue
        legacy_or_unbound_settlements += 1
        hardened.append(_quarantine_settlement(event))

    result = analyze_weather_paper_capital_lock(hardened)
    result["settlement_provenance_version"] = "SELF_VERIFYING_V4"
    result["legacy_or_unbound_settlement_events_quarantined"] = legacy_or_unbound_settlements
    result["settlement_validation"] = (
        "SELF_VERIFYING_V4_RECONCILER_EVENT_DIGEST_BOUND_TO_"
        "TRADE_GROUP_MARKET_SETTLEMENT_KEY_PROVIDER_SOURCE_FINGERPRINT_"
        "SIDE_QUANTITY_PAYOUT_SETTLEMENT_TIMESTAMP_PLUS_"
        "EXACT_KEY_CATEGORY_MARKET_BINARY_TERMINAL_PAYOUT_AND_FINITE_DECIMALS"
    )
    return result
