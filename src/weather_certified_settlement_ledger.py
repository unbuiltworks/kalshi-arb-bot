"""Create identity-bound weather settlement ledger events from audited reconciliation.

Research/PAPER accounting only. This module is the narrow handoff between
``reconcile_weather_paper_fill`` and the shared cross-venue ledger. It never calls a
venue and has no order-entry path.

A successful reconciliation is necessary but not sufficient: the caller must also
supply the exact synthetic trade-group/settlement identity and a later official
settlement timestamp. The emitted external id is deterministic and self-verifiable:
it binds the trade group, exact market, settlement key, terminal provider, terminal
source URL fingerprint, side, quantity, payout, and settlement timestamp. This
prevents a valid provenance token from being replayed onto a different terminal
settlement payload for the same trade identity.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any

from src.cross_venue_ledger import LedgerEvent, make_event

D = Decimal
PREFIX = "weather-certified-settlement:v4:"


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_upper(value: Any) -> str:
    return _norm(value).upper()


def _norm_url(value: Any) -> str:
    return _norm(value).rstrip("/").lower()


def _provider_token(provider: str) -> str:
    token = _norm_upper(provider)
    if not token or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token):
        raise ValueError("settlement provider must be a non-empty token")
    return token


def _source_url_hash(source_url: str) -> str:
    normalized = _norm_url(source_url)
    if not normalized:
        raise ValueError("terminal settlement source URL is required")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _decimal_token(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("finite Decimal required for certified settlement payload")
    return format(value.normalize(), "f")


def certified_weather_settlement_external_id(
    *,
    trade_group_id: str,
    market_id: str,
    settlement_key: str,
    provider: str,
    source_url: str,
    side: str,
    quantity: Decimal,
    payout: Decimal,
    settlement_ts_ms: int,
) -> str:
    group = _norm(trade_group_id)
    market = _norm_upper(market_id)
    key = _norm(settlement_key)
    provider_token = _provider_token(provider)
    source_hash = _source_url_hash(source_url)
    side_token = _norm_upper(side)
    if not group or not market or not key:
        raise ValueError("all certified settlement identity fields are required")
    if side_token not in {"YES", "NO"}:
        raise ValueError("certified settlement side must be YES or NO")
    if not isinstance(settlement_ts_ms, int) or isinstance(settlement_ts_ms, bool) or settlement_ts_ms < 0:
        raise ValueError("settlement_ts_ms must be a nonnegative integer")
    quantity_token = _decimal_token(quantity)
    payout_token = _decimal_token(payout)
    digest = sha256(
        "\x1f".join(
            (
                group,
                market,
                key,
                provider_token,
                source_hash,
                side_token,
                quantity_token,
                payout_token,
                str(settlement_ts_ms),
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"{PREFIX}{provider_token}:{source_hash}:{digest}"


def ledger_event_from_weather_reconciliation(
    *,
    reconciliation: dict[str, Any],
    trade_group_id: str,
    settlement_key: str,
    market_id: str,
    settlement_ts_ms: int,
) -> LedgerEvent:
    """Emit one exact, audited settlement event or fail closed.

    ``reconciliation`` must be the successful result of
    ``reconcile_weather_paper_fill``. No attempt is made to infer or repair missing
    provenance here.
    """
    if not isinstance(reconciliation, dict) or reconciliation.get("reconciled") is not True:
        raise ValueError("weather settlement reconciliation must be explicitly successful")
    if reconciliation.get("category") != "weather" or reconciliation.get("classification") != "PAPER":
        raise ValueError("weather PAPER reconciliation required")
    if reconciliation.get("order_entry") is not False or reconciliation.get("live_probe_ready") is not False:
        raise ValueError("reconciliation must remain read-only and not live-ready")
    if not isinstance(settlement_ts_ms, int) or isinstance(settlement_ts_ms, bool) or settlement_ts_ms < 0:
        raise ValueError("settlement_ts_ms must be a nonnegative integer")

    exact_market = _norm_upper(market_id)
    reconciled_market = _norm_upper(reconciliation.get("market_ticker"))
    if not exact_market or reconciled_market != exact_market:
        raise ValueError("reconciled exact market does not match ledger market_id")

    side = _norm_upper(reconciliation.get("side"))
    outcome = _norm_upper(reconciliation.get("settlement_outcome"))
    quantity = _finite_decimal(reconciliation.get("quantity"))
    if side not in {"YES", "NO"} or outcome not in {"YES", "NO"}:
        raise ValueError("terminal side/outcome must be binary")
    if quantity is None or quantity <= 0 or quantity != quantity.to_integral_value():
        raise ValueError("settled paper quantity must be a positive whole contract count")

    payout_cents = _finite_decimal(reconciliation.get("paper_payout_cents"))
    expected_payout_cents = D("100") * quantity if side == outcome else D("0")
    if payout_cents is None or payout_cents != expected_payout_cents:
        raise ValueError("reconciled payout is not the exact binary terminal payout")
    payout = payout_cents / D("100")

    provider = _provider_token(reconciliation.get("settlement_source_provider"))
    source_url = _norm_url(reconciliation.get("settlement_source_url"))
    external_id = certified_weather_settlement_external_id(
        trade_group_id=trade_group_id,
        market_id=exact_market,
        settlement_key=settlement_key,
        provider=provider,
        source_url=source_url,
        side=side,
        quantity=quantity,
        payout=payout,
        settlement_ts_ms=settlement_ts_ms,
    )

    return make_event(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category="weather",
        venue="kalshi",
        market_id=exact_market,
        event_type="settlement",
        side=side,
        quantity=quantity,
        payout=payout,
        external_id=external_id,
        ts_ms=settlement_ts_ms,
    )
