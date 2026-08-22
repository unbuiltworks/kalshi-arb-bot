"""Fail-closed binding for exact Kalshi weather fee evidence.

A numeric fee plus a boolean `verified` flag is not enough to establish exact
fixed-point after-fee economics.  The fee evidence must be bound to the exact
market, quote observation, side, executable ask and quantity that it is used to
score.  This module is read-only and has no order-entry capability.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


BLOCKER = "EXACT_NET_FEE_CONTEXT_UNBOUND"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _price(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0 or parsed >= 1:
        return None
    if -parsed.as_tuple().exponent > 4:
        return None
    return parsed


def _quantity(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return None
    if -parsed.as_tuple().exponent > 2:
        return None
    return parsed


def _expected_book_side(quote: dict[str, Any], side: str) -> tuple[Decimal, Decimal] | None:
    side_l = side.lower()
    p = _price(quote.get(f"{side_l}_ask_dollars"))
    q = _quantity(quote.get(f"{side_l}_ask_qty_fp"))
    if p is not None and q is not None:
        return p, q

    cents = quote.get(f"{side_l}_ask_cents")
    qty = quote.get(f"{side_l}_ask_qty")
    if (
        isinstance(cents, int)
        and not isinstance(cents, bool)
        and 1 <= cents <= 99
        and isinstance(qty, int)
        and not isinstance(qty, bool)
        and qty > 0
    ):
        return Decimal(cents) / Decimal(100), Decimal(qty)
    return None


def validate_weather_exact_fee_context(
    *,
    quote: dict[str, Any],
    fee_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate exact-net-fee evidence against the exact executable quote.

    Required binding:
    - literal context_verified=True;
    - exact market ticker and quote observation id/timestamp match;
    - each quoted side used by the context has the same ask and quantity;
    - finite non-negative exact net fee;
    - evidence source is an explicit Kalshi fill/order-fee reconciliation, not a
      public quadratic estimate.

    The function returns the validated side->fee mapping only when all supplied
    fee rows pass.  It never grants live readiness.
    """
    blockers: list[str] = []
    ctx = fee_context if isinstance(fee_context, dict) else {}

    if ctx.get("context_verified") is not True:
        blockers.append(BLOCKER)

    quote_market = quote.get("market_ticker")
    quote_obs = quote.get("quote_observed_at")
    if not isinstance(quote_market, str) or not quote_market.strip():
        blockers.append("QUOTE_MARKET_IDENTITY_MISSING")
    if not isinstance(quote_obs, str) or not quote_obs.strip():
        blockers.append("QUOTE_OBSERVATION_IDENTITY_MISSING")
    if ctx.get("market_ticker") != quote_market:
        blockers.append("EXACT_NET_FEE_MARKET_MISMATCH")
    if ctx.get("quote_observed_at") != quote_obs:
        blockers.append("EXACT_NET_FEE_QUOTE_MISMATCH")

    source = ctx.get("evidence_source")
    if source not in {"KALSHI_FILL_RECONCILIATION", "KALSHI_ORDER_FEE_RECONCILIATION"}:
        blockers.append("EXACT_NET_FEE_SOURCE_UNVERIFIED")

    side_rows = ctx.get("sides")
    if not isinstance(side_rows, dict) or not side_rows:
        blockers.append(BLOCKER)
        side_rows = {}

    validated: dict[str, str] = {}
    for side, row in side_rows.items():
        if side not in {"YES", "NO"} or not isinstance(row, dict):
            blockers.append(BLOCKER)
            continue
        expected = _expected_book_side(quote, side)
        if expected is None:
            blockers.append(f"EXACT_NET_FEE_SIDE_NOT_IN_BOOK:{side}")
            continue
        expected_price, expected_qty = expected
        context_price = _price(row.get("ask_dollars"))
        context_qty = _quantity(row.get("quantity_fp"))
        fee = _decimal(row.get("net_fee_dollars"))
        if context_price != expected_price:
            blockers.append(f"EXACT_NET_FEE_PRICE_MISMATCH:{side}")
        if context_qty != expected_qty:
            blockers.append(f"EXACT_NET_FEE_QUANTITY_MISMATCH:{side}")
        if fee is None or fee < 0:
            blockers.append(f"EXACT_NET_FEE_INVALID:{side}")
        if (
            context_price == expected_price
            and context_qty == expected_qty
            and fee is not None
            and fee >= 0
        ):
            validated[side] = format(fee, "f")

    blockers = sorted(set(blockers))
    verified = not blockers and bool(validated)
    return {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "exact_fee_context_verified": verified,
        "exact_net_fee_dollars": validated if verified else {},
        "blockers": blockers,
    }
