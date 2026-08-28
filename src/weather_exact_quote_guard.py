"""Independent exact-orderbook and freshness audit for read-only weather economics.

Kalshi orderbooks expose bids for YES and NO. Executable taker asks are derived from the
opposite-side best bid: YES ask = 100 - best NO bid, and vice versa. Current Kalshi REST
responses use ``orderbook_fp.yes_dollars`` / ``no_dollars`` fixed-point strings; the
legacy integer-cent ``orderbook.yes`` / ``no`` representation is accepted only as a
backward-compatible research fixture. Prices that cannot be represented exactly on the
existing integer-cent weather economics schema fail closed rather than being rounded.

This guard binds the quote used for modeled weather EV to the exact market orderbook
response and independently checks quote age from observed timestamps. It contains no
order-entry functionality.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote, urlparse


def _aware(value: Any) -> datetime | None:
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


def _best_bid_legacy(levels: Any) -> tuple[int, int] | None:
    if not isinstance(levels, list):
        return None
    valid: list[tuple[int, int]] = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price, qty = level[0], level[1]
        if (
            isinstance(price, int)
            and not isinstance(price, bool)
            and 1 <= price <= 99
            and isinstance(qty, int)
            and not isinstance(qty, bool)
            and qty > 0
        ):
            valid.append((price, qty))
    return max(valid, key=lambda item: item[0]) if valid else None


def _fixed_point_level(level: Any) -> tuple[int, int] | None:
    """Return an exact integer-cent/integer-contract level or fail closed.

    Kalshi's current orderbook uses decimal-dollar strings for price and fixed-point
    contract counts. The surrounding weather economics schema is intentionally still
    integer-cent/integer-contract, so this adapter never silently rounds sub-cent prices
    or fractional quantities.
    """
    if not isinstance(level, (list, tuple)) or len(level) < 2:
        return None
    try:
        price_dollars = Decimal(str(level[0]))
        qty_fp = Decimal(str(level[1]))
    except (InvalidOperation, ValueError):
        return None
    cents = price_dollars * Decimal("100")
    if cents != cents.to_integral_value() or qty_fp != qty_fp.to_integral_value():
        return None
    price = int(cents)
    qty = int(qty_fp)
    if not (1 <= price <= 99) or qty <= 0:
        return None
    return price, qty


def _best_bid_fp(levels: Any) -> tuple[int, int] | None:
    if not isinstance(levels, list):
        return None
    valid = [parsed for level in levels if (parsed := _fixed_point_level(level)) is not None]
    return max(valid, key=lambda item: item[0]) if valid else None


def _extract_best_bids(payload: dict[str, Any]) -> tuple[tuple[int, int] | None, tuple[int, int] | None, str]:
    current = payload.get("orderbook_fp")
    if isinstance(current, dict):
        return (
            _best_bid_fp(current.get("yes_dollars")),
            _best_bid_fp(current.get("no_dollars")),
            "orderbook_fp",
        )

    legacy = payload.get("orderbook") if isinstance(payload.get("orderbook"), dict) else payload
    if isinstance(legacy, dict):
        return _best_bid_legacy(legacy.get("yes")), _best_bid_legacy(legacy.get("no")), "legacy_orderbook"
    return None, None, "unrecognized"


def audit_exact_weather_quote(
    *,
    target_market_ticker: Any,
    quote: dict[str, Any],
    api_request_url: Any,
    api_orderbook_response: Any,
    orderbook_observed_at: Any,
    audit_observed_at: Any,
    max_quote_age_ms: int = 2_000,
) -> dict[str, Any]:
    """Verify exact market, executable top-of-book, depth, and independently computed age."""
    blockers: list[str] = []
    ticker = str(target_market_ticker or "").strip().upper()

    try:
        parsed = urlparse(str(api_request_url or ""))
        segments = [unquote(x).strip().upper() for x in parsed.path.split("/") if x]
        exact_path = (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower().endswith("kalshi.com")
            and len(segments) >= 5
            and segments[-5:] == ["TRADE-API", "V2", "MARKETS", ticker, "ORDERBOOK"]
        )
    except Exception:
        exact_path = False
    if not ticker or not exact_path:
        blockers.append("EXACT_MARKET_ORDERBOOK_IDENTITY_UNVERIFIED")

    payload = api_orderbook_response if isinstance(api_orderbook_response, dict) else {}
    yes_bid, no_bid, representation = _extract_best_bids(payload)

    expected_yes = (100 - no_bid[0], no_bid[1]) if no_bid else None
    expected_no = (100 - yes_bid[0], yes_bid[1]) if yes_bid else None
    actual_yes = (
        quote.get("yes_ask_cents", quote.get("ask_cents")),
        quote.get("yes_ask_qty", quote.get("ask_qty")),
    )
    actual_no = (quote.get("no_ask_cents"), quote.get("no_ask_qty"))

    if representation == "unrecognized":
        blockers.append("EXACT_MARKET_ORDERBOOK_SCHEMA_UNRECOGNIZED")
    if expected_yes is None and expected_no is None:
        blockers.append("EXACT_MARKET_ORDERBOOK_EMPTY")
    if expected_yes is not None and actual_yes != expected_yes:
        blockers.append("YES_EXECUTABLE_QUOTE_DEPTH_MISMATCH")
    if expected_no is not None and actual_no != expected_no:
        blockers.append("NO_EXECUTABLE_QUOTE_DEPTH_MISMATCH")

    observed = _aware(orderbook_observed_at)
    quote_observed = _aware(quote.get("observed_at"))
    audited = _aware(audit_observed_at)
    age_ms: float | None = None
    valid_max_age = (
        isinstance(max_quote_age_ms, int)
        and not isinstance(max_quote_age_ms, bool)
        and max_quote_age_ms >= 0
    )
    if observed is None or quote_observed is None or audited is None or observed != quote_observed or not valid_max_age:
        blockers.append("ORDERBOOK_QUOTE_TIME_UNVERIFIED")
    else:
        age_ms = (audited - observed).total_seconds() * 1000.0
        if age_ms < 0:
            blockers.append("ORDERBOOK_QUOTE_TIME_UNVERIFIED")
        elif age_ms > max_quote_age_ms:
            blockers.append("QUOTE_STALE")
        supplied_age = quote.get("quote_age_ms")
        if (
            not isinstance(supplied_age, (int, float))
            or isinstance(supplied_age, bool)
            or abs(float(supplied_age) - age_ms) > 1.0
        ):
            blockers.append("QUOTE_AGE_MISMATCH")

    blockers = sorted(set(blockers))
    return {
        "exact_quote_verified": not blockers,
        "blockers": blockers,
        "orderbook_representation": representation,
        "derived_yes_ask_cents": expected_yes[0] if expected_yes else None,
        "derived_yes_ask_qty": expected_yes[1] if expected_yes else None,
        "derived_no_ask_cents": expected_no[0] if expected_no else None,
        "derived_no_ask_qty": expected_no[1] if expected_no else None,
        "computed_quote_age_ms": age_ms,
    }
