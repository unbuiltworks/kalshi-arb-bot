"""Recomputable identity-bound weather PAPER settlement ledger events.

V3 keeps settlement provider/source provenance inside the external id so downstream
accounting can independently parse and recompute the SHA-256 binding from the actual
ledger event identity. This closes the v2 gap where any 64-hex digest-shaped suffix
could pass a format-only provenance check.

Research/PAPER accounting only; no venue client or order-entry path exists here.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from src.cross_venue_ledger import LedgerEvent, make_event

D = Decimal
PREFIX = "weather-certified-settlement:v3:"


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


def _b64(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _official_provider_source(provider: str, source_url: str) -> bool:
    try:
        parsed = urlparse(source_url)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/").lower()
    provider = _norm_upper(provider)
    if provider == "THE_WEATHER_COMPANY":
        return host in {"weather.com", "www.weather.com"} and path == "/kalshi"
    if provider == "NWS":
        return host == "weather.gov" or host.endswith(".weather.gov") or host == "www.weather.gov"
    return False


def certified_weather_settlement_external_id_v3(
    *,
    trade_group_id: str,
    market_id: str,
    settlement_key: str,
    provider: str,
    source_url: str,
) -> str:
    provider_n = _norm_upper(provider)
    source_n = _norm_url(source_url)
    parts = (
        _norm(trade_group_id),
        _norm_upper(market_id),
        _norm(settlement_key),
        provider_n,
        source_n,
    )
    if any(not part for part in parts):
        raise ValueError("all certified settlement identity fields are required")
    if not _official_provider_source(provider_n, source_n):
        raise ValueError("unsupported or unofficial terminal weather settlement source")
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{PREFIX}{_b64(provider_n)}:{_b64(source_n)}:{digest}"


def parse_and_verify_weather_settlement_external_id_v3(event: LedgerEvent) -> dict[str, str] | None:
    external_id = event.external_id
    if not isinstance(external_id, str) or not external_id.startswith(PREFIX):
        return None
    payload = external_id[len(PREFIX):]
    pieces = payload.split(":")
    if len(pieces) != 3:
        return None
    provider_token, source_token, digest = pieces
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return None
    try:
        provider = _norm_upper(_unb64(provider_token))
        source_url = _norm_url(_unb64(source_token))
    except Exception:
        return None
    if not _official_provider_source(provider, source_url):
        return None
    expected = certified_weather_settlement_external_id_v3(
        trade_group_id=event.trade_group_id,
        market_id=event.market_id,
        settlement_key=event.settlement_key,
        provider=provider,
        source_url=source_url,
    )
    if expected != external_id:
        return None
    return {"provider": provider, "source_url": source_url}


def ledger_event_from_weather_reconciliation_v3(
    *,
    reconciliation: dict[str, Any],
    trade_group_id: str,
    settlement_key: str,
    market_id: str,
    settlement_ts_ms: int,
) -> LedgerEvent:
    if not isinstance(reconciliation, dict) or reconciliation.get("reconciled") is not True:
        raise ValueError("weather settlement reconciliation must be explicitly successful")
    if reconciliation.get("category") != "weather" or reconciliation.get("classification") != "PAPER":
        raise ValueError("weather PAPER reconciliation required")
    if reconciliation.get("order_entry") is not False or reconciliation.get("live_probe_ready") is not False:
        raise ValueError("reconciliation must remain read-only and not live-ready")
    if not isinstance(settlement_ts_ms, int) or isinstance(settlement_ts_ms, bool) or settlement_ts_ms < 0:
        raise ValueError("settlement_ts_ms must be a nonnegative integer")

    exact_market = _norm_upper(market_id)
    if not exact_market or _norm_upper(reconciliation.get("market_ticker")) != exact_market:
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

    provider = _norm_upper(reconciliation.get("settlement_source_provider"))
    source_url = _norm_url(reconciliation.get("settlement_source_url"))
    external_id = certified_weather_settlement_external_id_v3(
        trade_group_id=trade_group_id,
        market_id=exact_market,
        settlement_key=settlement_key,
        provider=provider,
        source_url=source_url,
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
        payout=payout_cents / D("100"),
        external_id=external_id,
        ts_ms=settlement_ts_ms,
    )
