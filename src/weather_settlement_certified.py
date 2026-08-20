"""Read-only Kalshi weather settlement certification helpers.

This module intentionally contains no order-entry code. It converts current Kalshi
Series/market metadata plus observed executable quotes into fail-closed SHADOW rows
suitable for the universal ledger. Forecast models are probability inputs only;
they are never treated as settlement sources.
"""
from __future__ import annotations

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


def _fee_cents(count: int, price_cents: int, fee_type: str, fee_multiplier: Any) -> int:
    """Current Series-metadata fee model; fail closed on unknown fee types.

    Kalshi standard quadratic taker fee is 0.07 * multiplier * C * p * (1-p),
    rounded up to the next cent. fee_multiplier is a multiplier, not the 0.07 rate.
    """
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
                     max_quote_age_ms: int = DEFAULT_MAX_QUOTE_AGE_MS) -> dict[str, Any]:
    """Build one read-only weather SHADOW row; never emits live readiness.

    Verification flags must be literal booleans. Executable quotes must also be
    fresh enough for the configured read-only shadow threshold. Both YES and NO
    executable asks are modeled independently; legacy ``ask_cents``/``ask_qty``
    aliases are interpreted as YES only. We never infer an executable NO price
    from a YES mid/chance or from non-executable display fields.
    """
    blockers: list[str] = []
    _, series_blockers = certify_series(series)
    blockers.extend(series_blockers)

    station_verified = station.get("identity_verified") is True
    local_day_verified = market.get("local_day_verified") is True
    bracket_verified = market.get("bracket_semantics_verified") is True
    if not station_verified or not str(station.get("cli_product", "")).startswith("CLI"):
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

    model_probability = None
    if forecast is not None:
        try:
            model_probability = Decimal(str(forecast.get("bracket_probability")))
            if not model_probability.is_finite() or model_probability < 0 or model_probability > 1:
                raise ValueError
        except Exception:
            model_probability = None

    side_economics: list[dict[str, Any]] = []
    if "FEE_UNVERIFIED" not in blockers:
        try:
            if yes_valid:
                side_economics.append(_side_economics(
                    side="YES", price_cents=yes_ask, qty=yes_qty, yes_probability=model_probability,
                    fee_type=str(series.get("fee_type")), fee_multiplier=series.get("fee_multiplier"),
                ))
            if no_valid:
                side_economics.append(_side_economics(
                    side="NO", price_cents=no_ask, qty=no_qty, yes_probability=model_probability,
                    fee_type=str(series.get("fee_type")), fee_multiplier=series.get("fee_multiplier"),
                ))
        except Exception:
            blockers.append("FEE_UNVERIFIED")
            side_economics = []

    best_side = None
    if side_economics:
        ranked = [x for x in side_economics if x["modeled_after_fee_ev_cents"] is not None]
        if ranked:
            best_side = max(ranked, key=lambda x: Decimal(x["modeled_after_fee_ev_cents"]))

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
        "yes_ask_cents": yes_ask if yes_valid else None,
        "yes_available_contracts": yes_qty if yes_valid else None,
        "no_ask_cents": no_ask if no_valid else None,
        "no_available_contracts": no_qty if no_valid else None,
        # Backward-compatible YES aliases for existing ledger consumers.
        "ask_cents": yes_ask if yes_valid else None,
        "available_contracts": yes_qty if yes_valid else None,
        "quote_age_ms": age_ms,
        "max_quote_age_ms": max_quote_age_ms,
        "fee_type": series.get("fee_type"),
        "fee_multiplier": series.get("fee_multiplier"),
        "forecast_bracket_probability": str(model_probability) if model_probability is not None else None,
        "side_economics": side_economics,
        "best_shadow_side": best_side,
        # Backward-compatible YES-only fields; never substitute NO economics here.
        "modeled_taker_fee_cents": next((x["modeled_taker_fee_cents"] for x in side_economics if x["side"] == "YES"), None),
        "modeled_after_fee_ev_cents": next((x["modeled_after_fee_ev_cents"] for x in side_economics if x["side"] == "YES"), None),
        "blockers": blockers,
        "settlement_certified_shadow": len(blockers) == 0,
    }
