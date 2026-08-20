"""Read-only Kalshi weather settlement certification helpers.

This module intentionally contains no order-entry code. It converts current Kalshi
Series/market metadata plus observed executable quotes into fail-closed SHADOW rows
suitable for the universal ledger. Forecast models are probability inputs only;
they are never treated as settlement sources.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from typing import Any, Iterable
from urllib.parse import urlparse

from src.weather_fee_history_guard import resolve_fee_at_quote

HARD_BLOCKERS = {
    "RULES_PENDING",
    "STATION_IDENTITY_UNVERIFIED",
    "LOCAL_DAY_UNVERIFIED",
    "BRACKET_SEMANTICS_UNVERIFIED",
    "FEE_UNVERIFIED",
    "FEE_SCHEDULE_UNVERIFIED",
    "FEE_HISTORY_UNVERIFIED",
    "SETTLEMENT_SOURCE_UNVERIFIED",
    "BOOK_UNVERIFIED",
    "QUOTE_STALE",
}

DEFAULT_MAX_QUOTE_AGE_MS = 2_000


def _official_https(url: str, hosts: Iterable[str]) -> bool:
    try:
        p = urlparse(str(url))
    except Exception:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


def _settlement_source_provider(source: Any) -> str | None:
    """Recognize only currently audited official weather settlement endpoints.

    Daily temperature Series historically named NWS/weather.gov directly. Current
    Series metadata (effective 2026-08-14) names The Weather Company and links
    ``https://weather.com/kalshi``. We trust the Series metadata for settlement
    provenance; we do not infer a provider from legacy strategy configuration.
    """
    if not isinstance(source, dict):
        return None
    url = str(source.get("url") or "").strip()
    name = str(source.get("name") or "").strip().lower()
    if _official_https(url, ("weather.gov",)):
        return "NWS"
    if _official_https(url, ("weather.com",)):
        try:
            path = (urlparse(url).path or "").rstrip("/").lower()
        except Exception:
            return None
        if path == "/kalshi" and ("weather company" in name or not name):
            return "THE_WEATHER_COMPANY"
    return None


def _fee_cents(count: int, price_cents: int, fee_type: str, fee_multiplier: Any) -> int:
    """Current Series-metadata fee model; fail closed on unknown fee types."""
    if fee_type != "quadratic":
        raise ValueError("unsupported fee_type")
    m = Decimal(str(fee_multiplier))
    if not m.is_finite() or m < 0:
        raise ValueError("invalid fee_multiplier")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("invalid count")
    if not isinstance(price_cents, int) or isinstance(price_cents, bool) or not (1 <= price_cents <= 99):
        raise ValueError("invalid price")
    p = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * m * Decimal(count) * p * (Decimal(1) - p)
    return int((raw * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def certify_series(series: dict[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    sources = series.get("settlement_sources") or []
    if not sources or not any(_settlement_source_provider(s) for s in sources):
        blockers.append("SETTLEMENT_SOURCE_UNVERIFIED")
    terms = str(series.get("contract_terms_url") or "")
    if not _official_https(terms, ("kalshi.com", "assets.kalshi.com", "kalshi-public-docs.s3.amazonaws.com")):
        blockers.append("RULES_PENDING")
    if series.get("fee_type") != "quadratic" or series.get("fee_multiplier") is None:
        blockers.append("FEE_UNVERIFIED")
    else:
        try:
            m = Decimal(str(series.get("fee_multiplier")))
            if not m.is_finite() or m < 0:
                raise ValueError
        except Exception:
            blockers.append("FEE_UNVERIFIED")
    return not blockers, sorted(set(blockers))


def _valid_level(price: Any, qty: Any) -> bool:
    return (
        isinstance(price, int) and not isinstance(price, bool) and 1 <= price <= 99
        and isinstance(qty, int) and not isinstance(qty, bool) and qty > 0
    )


def _aware_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _resolve_fee_metadata(
    series: dict[str, Any],
    *,
    fee_changes: list[dict[str, Any]] | None,
    observed_at: Any,
    historical_complete: bool = False,
) -> tuple[str | None, Any, str | None, list[str]]:
    """Resolve exact fee metadata at quote time without leaking current fees into history."""
    resolved = resolve_fee_at_quote(
        series,
        fee_changes=fee_changes,
        observed_at=observed_at,
        historical_complete=historical_complete,
    )
    return (
        resolved.get("fee_type"),
        resolved.get("fee_multiplier"),
        resolved.get("source"),
        list(resolved.get("blockers") or []),
    )


def _forecast_probability(
    forecast: dict[str, Any] | None,
    *,
    expected_cli_product: str,
) -> tuple[Decimal | None, list[str]]:
    if forecast is None:
        return None, ["FORECAST_UNVERIFIED"]

    blockers: list[str] = []
    model = str(forecast.get("model") or "").strip()
    forecast_cli = str(forecast.get("cli_product") or "").strip().upper()
    expected_cli = str(expected_cli_product or "").strip().upper()
    run_time = _aware_iso(forecast.get("model_run_time"))
    valid_time = _aware_iso(forecast.get("forecast_valid_time"))

    if not model:
        blockers.append("FORECAST_UNVERIFIED")
    if forecast.get("station_mapping_verified") is not True:
        blockers.append("FORECAST_STATION_MAPPING_UNVERIFIED")
    if not expected_cli or forecast_cli != expected_cli:
        blockers.append("FORECAST_STATION_ID_MISMATCH")
    if forecast.get("freshness_verified") is not True:
        blockers.append("FORECAST_STALE_OR_UNVERIFIED")
    if run_time is None or valid_time is None or run_time > valid_time:
        blockers.append("FORECAST_TIME_UNVERIFIED")

    try:
        probability = Decimal(str(forecast.get("bracket_probability")))
        if not probability.is_finite() or probability < 0 or probability > 1:
            raise ValueError
    except Exception:
        probability = None
        blockers.append("FORECAST_UNVERIFIED")

    blockers = sorted(set(blockers))
    return (probability if not blockers else None), blockers


def _side_economics(*, side: str, price_cents: int, qty: int, yes_probability: Decimal | None,
                    fee_type: str, fee_multiplier: Any) -> dict[str, Any]:
    fee = _fee_cents(qty, price_cents, fee_type, fee_multiplier)
    probability = None
    ev = None
    edge_per_contract = None
    if yes_probability is not None:
        probability = yes_probability if side == "YES" else Decimal(1) - yes_probability
        expected_payout_cents = probability * Decimal(100 * qty)
        ev = expected_payout_cents - Decimal(price_cents * qty + fee)
        edge_per_contract = ev / Decimal(qty)
    return {
        "side": side,
        "ask_cents": price_cents,
        "available_contracts": qty,
        "modeled_taker_fee_cents": fee,
        "forecast_side_probability": str(probability) if probability is not None else None,
        "modeled_after_fee_ev_cents": str(ev) if ev is not None else None,
        "modeled_after_fee_edge_cents_per_contract": str(edge_per_contract) if edge_per_contract is not None else None,
    }


def build_shadow_row(*, series: dict[str, Any], market: dict[str, Any], quote: dict[str, Any],
                     station: dict[str, Any], forecast: dict[str, Any] | None = None,
                     fee_changes: list[dict[str, Any]] | None = None,
                     fee_history_complete: bool = False,
                     max_quote_age_ms: int = DEFAULT_MAX_QUOTE_AGE_MS) -> dict[str, Any]:
    """Build one read-only weather SHADOW row; never emits live readiness."""
    blockers: list[str] = []
    _, series_blockers = certify_series(series)
    blockers.extend(series_blockers)

    station_verified = station.get("identity_verified") is True
    local_day_verified = market.get("local_day_verified") is True
    bracket_verified = market.get("bracket_semantics_verified") is True
    cli_product = str(station.get("cli_product", "")).strip().upper()
    if not station_verified or not cli_product.startswith("CLI"):
        blockers.append("STATION_IDENTITY_UNVERIFIED")
    if not local_day_verified:
        blockers.append("LOCAL_DAY_UNVERIFIED")
    if not bracket_verified:
        blockers.append("BRACKET_SEMANTICS_UNVERIFIED")

    yes_ask = quote.get("yes_ask_cents", quote.get("ask_cents"))
    yes_qty = quote.get("yes_ask_qty", quote.get("ask_qty"))
    no_ask = quote.get("no_ask_cents")
    no_qty = quote.get("no_ask_qty")
    age_ms = quote.get("quote_age_ms")
    observed_at = quote.get("observed_at")
    yes_valid = _valid_level(yes_ask, yes_qty)
    no_valid = _valid_level(no_ask, no_qty)
    if not yes_valid and not no_valid:
        blockers.append("BOOK_UNVERIFIED")

    valid_age = isinstance(age_ms, (int, float)) and not isinstance(age_ms, bool) and age_ms >= 0
    if not valid_age:
        blockers.append("BOOK_UNVERIFIED")
    else:
        if not isinstance(max_quote_age_ms, int) or isinstance(max_quote_age_ms, bool) or max_quote_age_ms < 0:
            blockers.append("BOOK_UNVERIFIED")
        elif age_ms > max_quote_age_ms:
            blockers.append("QUOTE_STALE")

    resolved_fee_type, resolved_fee_multiplier, fee_metadata_source, fee_schedule_blockers = _resolve_fee_metadata(
        series,
        fee_changes=fee_changes,
        observed_at=observed_at,
        historical_complete=fee_history_complete,
    )
    blockers.extend(fee_schedule_blockers)

    model_probability, forecast_blockers = _forecast_probability(
        forecast,
        expected_cli_product=cli_product,
    )

    side_economics: list[dict[str, Any]] = []
    fee_blocked = any(x in blockers for x in ("FEE_UNVERIFIED", "FEE_SCHEDULE_UNVERIFIED", "FEE_HISTORY_UNVERIFIED"))
    if not fee_blocked:
        try:
            if yes_valid:
                side_economics.append(_side_economics(
                    side="YES", price_cents=yes_ask, qty=yes_qty, yes_probability=model_probability,
                    fee_type=str(resolved_fee_type), fee_multiplier=resolved_fee_multiplier,
                ))
            if no_valid:
                side_economics.append(_side_economics(
                    side="NO", price_cents=no_ask, qty=no_qty, yes_probability=model_probability,
                    fee_type=str(resolved_fee_type), fee_multiplier=resolved_fee_multiplier,
                ))
        except Exception:
            blockers.append("FEE_UNVERIFIED")
            side_economics = []

    best_side = None
    if side_economics and not forecast_blockers:
        ranked = [x for x in side_economics if x["modeled_after_fee_ev_cents"] is not None]
        if ranked:
            best_side = max(ranked, key=lambda x: Decimal(x["modeled_after_fee_ev_cents"]))

    blockers = sorted(set(blockers))
    settlement_certified = len(blockers) == 0
    ev_certified = settlement_certified and not forecast_blockers and best_side is not None
    providers = sorted({p for p in (_settlement_source_provider(s) for s in (series.get("settlement_sources") or [])) if p})
    return {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "legacy_strategy_status": "UNVALIDATED_LEGACY_RESEARCH",
        "series_ticker": series.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "market_ticker": market.get("ticker"),
        "market_date": market.get("market_date"),
        "station_name": station.get("name"),
        "cli_product": cli_product or None,
        "settlement_sources": series.get("settlement_sources") or [],
        "settlement_source_providers": providers,
        "contract_terms_url": series.get("contract_terms_url"),
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "yes_ask_cents": yes_ask if yes_valid else None,
        "yes_available_contracts": yes_qty if yes_valid else None,
        "no_ask_cents": no_ask if no_valid else None,
        "no_available_contracts": no_qty if no_valid else None,
        "ask_cents": yes_ask if yes_valid else None,
        "available_contracts": yes_qty if yes_valid else None,
        "quote_age_ms": age_ms,
        "quote_observed_at": observed_at,
        "max_quote_age_ms": max_quote_age_ms,
        "fee_type": resolved_fee_type,
        "fee_multiplier": resolved_fee_multiplier,
        "fee_metadata_source": fee_metadata_source,
        "fee_schedule_checked": not any(x in blockers for x in ("FEE_SCHEDULE_UNVERIFIED", "FEE_HISTORY_UNVERIFIED")),
        "fee_history_complete": fee_history_complete is True,
        "forecast_model": forecast.get("model") if forecast else None,
        "forecast_cli_product": forecast.get("cli_product") if forecast else None,
        "forecast_model_run_time": forecast.get("model_run_time") if forecast else None,
        "forecast_valid_time": forecast.get("forecast_valid_time") if forecast else None,
        "forecast_bracket_probability": str(model_probability) if model_probability is not None else None,
        "forecast_blockers": forecast_blockers,
        "side_economics": side_economics,
        "best_shadow_side": best_side,
        "modeled_taker_fee_cents": next((x["modeled_taker_fee_cents"] for x in side_economics if x["side"] == "YES"), None),
        "modeled_after_fee_ev_cents": next((x["modeled_after_fee_ev_cents"] for x in side_economics if x["side"] == "YES"), None),
        "blockers": blockers,
        "settlement_certified_shadow": settlement_certified,
        "ev_certified_shadow": ev_certified,
    }
