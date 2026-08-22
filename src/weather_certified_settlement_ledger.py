"""Create identity-bound weather PAPER settlement events from audited reconciliation.

The v5 external ID is self-verifying downstream: it embeds the normalized audited
terminal source URL (URL-safe base64) and binds it, together with trade identity,
provider, side, quantity, payout and settlement timestamp, into a SHA-256 digest.
A downstream analyzer can therefore re-check both provider/source allowlisting and
terminal payload integrity without trusting free-form reconciliation metadata.

Research/PAPER accounting only; no venue write path exists here.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.cross_venue_ledger import LedgerEvent, make_event

D = Decimal
PREFIX = "weather-certified-settlement:v5:"


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


def _official_nws_cli_source(source_url: str) -> bool:
    try:
        parsed = urlparse(source_url)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not (host == "weather.gov" or host == "www.weather.gov" or host.endswith(".weather.gov")):
        return False
    if (parsed.path or "").rstrip("/").lower() != "/product.php":
        return False
    query = {k.lower(): [v.upper() for v in values] for k, values in parse_qs(parsed.query).items()}
    return "CLI" in query.get("product", []) and any(token.strip() for token in query.get("issuedby", []))


def _official_provider_source(provider: str, source_url: str) -> bool:
    try:
        parsed = urlparse(source_url)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/").lower()
    provider_token = _norm_upper(provider)
    if provider_token == "THE_WEATHER_COMPANY":
        return host in {"weather.com", "www.weather.com"} and path == "/kalshi"
    if provider_token == "NWS":
        return _official_nws_cli_source(source_url)
    return False


def _decimal_token(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("finite Decimal required for certified settlement payload")
    return format(value.normalize(), "f")


def _b64(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def certified_weather_settlement_external_id(
    *, trade_group_id: str, market_id: str, settlement_key: str, provider: str,
    source_url: str, side: str, quantity: Decimal, payout: Decimal,
    settlement_ts_ms: int,
) -> str:
    group = _norm(trade_group_id)
    market = _norm_upper(market_id)
    key = _norm(settlement_key)
    provider_token = _provider_token(provider)
    source_normalized = _norm_url(source_url)
    if not _official_provider_source(provider_token, source_normalized):
        raise ValueError("unsupported or unofficial terminal weather settlement source")
    side_token = _norm_upper(side)
    if not group or not market or not key:
        raise ValueError("all certified settlement identity fields are required")
    if side_token not in {"YES", "NO"}:
        raise ValueError("certified settlement side must be YES or NO")
    if not isinstance(settlement_ts_ms, int) or isinstance(settlement_ts_ms, bool) or settlement_ts_ms < 0:
        raise ValueError("settlement_ts_ms must be a nonnegative integer")
    quantity_token = _decimal_token(quantity)
    payout_token = _decimal_token(payout)
    digest = sha256("\x1f".join((group, market, key, provider_token, source_normalized,
        side_token, quantity_token, payout_token, str(settlement_ts_ms))).encode("utf-8")).hexdigest()
    return f"{PREFIX}{provider_token}:{_b64(source_normalized)}:{digest}"


def parse_and_verify_weather_settlement_external_id(event: LedgerEvent) -> dict[str, str] | None:
    """Independently verify v5 source authority and terminal-payload binding."""
    external_id = event.external_id
    if not isinstance(external_id, str) or not external_id.startswith(PREFIX):
        return None
    pieces = external_id[len(PREFIX):].split(":")
    if len(pieces) != 3:
        return None
    provider, source_token, digest = pieces
    try:
        if _provider_token(provider) != provider:
            return None
        source_url = _norm_url(_unb64(source_token))
    except Exception:
        return None
    if not _official_provider_source(provider, source_url):
        return None
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return None
    if event.side not in {"YES", "NO"}:
        return None
    if not isinstance(event.quantity, Decimal) or not event.quantity.is_finite() or event.quantity <= 0:
        return None
    if not isinstance(event.payout, Decimal) or not event.payout.is_finite():
        return None
    if not isinstance(event.ts_ms, int) or isinstance(event.ts_ms, bool) or event.ts_ms < 0:
        return None
    group = _norm(event.trade_group_id)
    market = _norm_upper(event.market_id)
    key = _norm(event.settlement_key)
    if not group or not market or not key:
        return None
    expected = certified_weather_settlement_external_id(
        trade_group_id=group,
        market_id=market,
        settlement_key=key,
        provider=provider,
        source_url=source_url,
        side=event.side,
        quantity=event.quantity,
        payout=event.payout,
        settlement_ts_ms=event.ts_ms,
    )
    if expected != external_id:
        return None
    return {"provider": provider, "source_url": source_url}


def ledger_event_from_weather_reconciliation(
    *, reconciliation: dict[str, Any], trade_group_id: str, settlement_key: str,
    market_id: str, settlement_ts_ms: int,
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
    payout = payout_cents / D("100")
    provider = _provider_token(reconciliation.get("settlement_source_provider"))
    source_url = _norm_url(reconciliation.get("settlement_source_url"))
    if not _official_provider_source(provider, source_url):
        raise ValueError("unsupported or unofficial terminal weather settlement source")
    external_id = certified_weather_settlement_external_id(
        trade_group_id=trade_group_id, market_id=exact_market, settlement_key=settlement_key,
        provider=provider, source_url=source_url, side=side, quantity=quantity,
        payout=payout, settlement_ts_ms=settlement_ts_ms,
    )
    return make_event(trade_group_id=trade_group_id, settlement_key=settlement_key,
        category="weather", venue="kalshi", market_id=exact_market,
        event_type="settlement", side=side, quantity=quantity, payout=payout,
        external_id=external_id, ts_ms=settlement_ts_ms)
