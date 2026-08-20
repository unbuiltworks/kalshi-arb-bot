"""Read-only Kalshi weather settlement certification helpers.

This module intentionally contains no order-entry code. It converts current Kalshi
Series/market metadata plus an observed executable quote into a fail-closed SHADOW
row suitable for the universal ledger. Forecast models are probability inputs only;
they are never treated as settlement sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any, Iterable
from urllib.parse import urlparse

HARD_BLOCKERS = {
    "RULES_PENDING",
    "STATION_IDENTITY_UNVERIFIED",
    "LOCAL_DAY_UNVERIFIED",
    "BRACKET_SEMANTICS_UNVERIFIED",
    "FEE_UNVERIFIED",
    "SETTLEMENT_SOURCE_UNVERIFIED",
    "BOOK_UNVERIFIED",
}


def _official_https(url: str, hosts: Iterable[str]) -> bool:
    try:
        p = urlparse(str(url))
    except Exception:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


def _fee_cents(count: int, price_cents: int, fee_type: str, fee_multiplier: Any) -> int:
    """Current Series-metadata fee model; fail closed on unknown fee types.

    Kalshi standard quadratic taker fee is 0.07 * multiplier * C * p * (1-p),
    rounded up to the next cent. fee_multiplier is a multiplier, not the 0.07 rate.
    """
    if fee_type != "quadratic":
        raise ValueError("unsupported fee_type")
    m = Decimal(str(fee_multiplier))
    if m < 0:
        raise ValueError("negative fee_multiplier")
    p = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * m * Decimal(count) * p * (Decimal(1) - p)
    return int((raw * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def certify_series(series: dict[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    sources = series.get("settlement_sources") or []
    if not sources or not any(
        "weather.gov" in str(s.get("url", "")).lower()
        and _official_https(str(s.get("url", "")), ("weather.gov",))
        for s in sources if isinstance(s, dict)
    ):
        blockers.append("SETTLEMENT_SOURCE_UNVERIFIED")
    terms = str(series.get("contract_terms_url") or "")
    if not _official_https(terms, ("kalshi.com", "kalshi-public-docs.s3.amazonaws.com")):
        blockers.append("RULES_PENDING")
    if series.get("fee_type") != "quadratic" or series.get("fee_multiplier") is None:
        blockers.append("FEE_UNVERIFIED")
    return not blockers, blockers


def build_shadow_row(*, series: dict[str, Any], market: dict[str, Any], quote: dict[str, Any],
                     station: dict[str, Any], forecast: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one read-only weather SHADOW row; never emits live readiness."""
    blockers: list[str] = []
    ok, series_blockers = certify_series(series)
    blockers.extend(series_blockers)

    if not station.get("identity_verified") or not str(station.get("cli_product", "")).startswith("CLI"):
        blockers.append("STATION_IDENTITY_UNVERIFIED")
    if not market.get("local_day_verified"):
        blockers.append("LOCAL_DAY_UNVERIFIED")
    if not market.get("bracket_semantics_verified"):
        blockers.append("BRACKET_SEMANTICS_UNVERIFIED")

    ask = quote.get("ask_cents")
    qty = quote.get("ask_qty")
    age_ms = quote.get("quote_age_ms")
    if not isinstance(ask, int) or not (1 <= ask <= 99) or not isinstance(qty, int) or qty <= 0:
        blockers.append("BOOK_UNVERIFIED")
    if not isinstance(age_ms, (int, float)) or age_ms < 0:
        blockers.append("BOOK_UNVERIFIED")

    fee = None
    after_fee_ev = None
    model_probability = None
    if "FEE_UNVERIFIED" not in blockers and isinstance(ask, int) and isinstance(qty, int) and qty > 0:
        try:
            fee = _fee_cents(qty, ask, str(series.get("fee_type")), series.get("fee_multiplier"))
        except Exception:
            blockers.append("FEE_UNVERIFIED")

    if forecast is not None:
        try:
            model_probability = Decimal(str(forecast.get("bracket_probability")))
            if model_probability < 0 or model_probability > 1:
                raise ValueError
        except Exception:
            model_probability = None
        if model_probability is not None and fee is not None and isinstance(ask, int) and isinstance(qty, int):
            expected_payout_cents = model_probability * Decimal(100 * qty)
            after_fee_ev = expected_payout_cents - Decimal(ask * qty + fee)

    blockers = sorted(set(blockers))
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
        "cli_product": station.get("cli_product"),
        "settlement_sources": series.get("settlement_sources") or [],
        "contract_terms_url": series.get("contract_terms_url"),
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "ask_cents": ask,
        "available_contracts": qty,
        "quote_age_ms": age_ms,
        "fee_type": series.get("fee_type"),
        "fee_multiplier": series.get("fee_multiplier"),
        "modeled_taker_fee_cents": fee,
        "forecast_bracket_probability": str(model_probability) if model_probability is not None else None,
        "modeled_after_fee_ev_cents": str(after_fee_ev) if after_fee_ev is not None else None,
        "blockers": blockers,
        "settlement_certified_shadow": len(blockers) == 0,
    }
