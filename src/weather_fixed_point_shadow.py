"""Read-only fixed-point weather quote economics for current Kalshi markets.

Kalshi's current API represents executable prices as fixed-point dollar strings and
contract quantities as fixed-point strings. This module deliberately keeps those
values as Decimal rather than coercing them back to integer cents/contracts.

The exchange's *base* quadratic trade fee can be computed from public Series metadata.
Exact net per-fill fees can also depend on fee-rounding, fill fragmentation, and the
per-order rounding accumulator. When exact fill context is unavailable, this module
therefore reports a deliberately conservative public-data fee upper bound rather than
pretending the base formula is the exact debit.

No order-entry clients, credentials, live-arm state, or execution paths exist here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any


FIXED_POINT_FEE_BLOCKER = "EXACT_NET_FEE_ROUNDING_UNVERIFIED"
MIN_CONTRACT_INCREMENT = Decimal("0.01")
TRADE_FEE_INCREMENT = Decimal("0.0001")
ROUNDING_ACCUMULATOR_RESIDUAL_UPPER = Decimal("0.01")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _price_dollars(value: Any) -> Decimal | None:
    p = _decimal(value)
    if p is None or p <= 0 or p >= 1:
        return None
    # Current Kalshi fixed-point prices support up to four decimals.
    if -p.as_tuple().exponent > 4:
        return None
    return p


def _quantity_fp(value: Any) -> Decimal | None:
    q = _decimal(value)
    if q is None or q <= 0:
        return None
    # Current Kalshi fixed-point contract quantities support up to two decimals.
    if -q.as_tuple().exponent > 2:
        return None
    return q


def _quadratic_raw_fee_dollars(*, quantity: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
    return Decimal("0.07") * multiplier * quantity * price * (Decimal(1) - price)


def _quadratic_base_fee_dollars(*, quantity: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
    raw = _quadratic_raw_fee_dollars(quantity=quantity, price=price, multiplier=multiplier)
    # Public Kalshi fee-rounding docs define trade-fee precision at $0.0001.
    return raw.quantize(TRADE_FEE_INCREMENT, rounding=ROUND_CEILING)


def _conservative_net_fee_upper_bound_dollars(
    *, quantity: Decimal, price: Decimal, multiplier: Decimal
) -> Decimal:
    """Upper-bound Kalshi taker net fee when exact fill partition is unknown.

    Public Kalshi documentation states that fixed-point quantities have 0.01-contract
    minimum granularity; each fill's trade fee is rounded up to $0.0001; and the
    per-order rounding accumulator rebates whole cents so residual accumulated rounding
    remains below one cent. We do not know how displayed depth is partitioned across
    resting orders, so assume the worst case: the incoming quantity is fragmented into
    as many 0.01-contract fills as possible.

    The resulting bound is intentionally loose but safe for read-only opportunity
    screening. It is *not* labeled an exact fee and cannot unlock live readiness.
    """
    raw = _quadratic_raw_fee_dollars(quantity=quantity, price=price, multiplier=multiplier)
    max_fills = (quantity / MIN_CONTRACT_INCREMENT).to_integral_value(rounding=ROUND_CEILING)
    per_fill_rounding_overhead = Decimal(max_fills) * TRADE_FEE_INCREMENT
    upper = raw + per_fill_rounding_overhead + ROUNDING_ACCUMULATOR_RESIDUAL_UPPER
    return upper.quantize(TRADE_FEE_INCREMENT, rounding=ROUND_CEILING)


def normalize_fixed_point_book(quote: dict[str, Any]) -> dict[str, Any]:
    """Normalize executable YES/NO asks without inventing the missing opposite side.

    Preferred fields are current fixed-point values:
      yes_ask_dollars / yes_ask_qty_fp
      no_ask_dollars  / no_ask_qty_fp

    Legacy whole-cent/whole-contract fields are accepted only when both values are
    literal integers. They are converted losslessly to Decimal research values.
    """
    sides: dict[str, dict[str, str]] = {}
    blockers: list[str] = []

    for side in ("yes", "no"):
        p = _price_dollars(quote.get(f"{side}_ask_dollars"))
        q = _quantity_fp(quote.get(f"{side}_ask_qty_fp"))
        source = "fixed_point"

        if p is None or q is None:
            cents = quote.get(f"{side}_ask_cents")
            qty = quote.get(f"{side}_ask_qty")
            if (
                isinstance(cents, int) and not isinstance(cents, bool) and 1 <= cents <= 99
                and isinstance(qty, int) and not isinstance(qty, bool) and qty > 0
            ):
                p = Decimal(cents) / Decimal(100)
                q = Decimal(qty)
                source = "legacy_integer_exact"

        if p is not None and q is not None:
            sides[side.upper()] = {
                "ask_dollars": format(p, "f"),
                "available_contracts_fp": format(q, "f"),
                "book_representation": source,
            }

    if not sides:
        blockers.append("FIXED_POINT_BOOK_UNVERIFIED")

    return {
        "read_only": True,
        "order_entry": False,
        "sides": sides,
        "blockers": blockers,
    }


def build_fixed_point_research_economics(
    *,
    quote: dict[str, Any],
    fee_type: Any,
    fee_multiplier: Any,
    yes_probability: Any = None,
    exact_net_fee_dollars: dict[str, Any] | None = None,
    exact_fee_context_verified: bool = False,
) -> dict[str, Any]:
    """Compute fixed-point depth and conservative read-only after-fee economics.

    Without verified exact net fee context, the public fee mechanics still support a
    worst-case fill-fragmentation upper bound. That bound may be used to report a
    conservative after-fee model-EV *lower bound* while exact after-fee economics stay
    blocked. A caller may separately supply an independently reconciled exact net fee;
    only literal ``exact_fee_context_verified=True`` permits exact fee-adjusted EV.
    """
    book = normalize_fixed_point_book(quote)
    blockers = list(book["blockers"])
    rows: list[dict[str, Any]] = []

    if fee_type != "quadratic":
        blockers.append("FEE_UNVERIFIED")
        multiplier = None
    else:
        multiplier = _decimal(fee_multiplier)
        if multiplier is None or multiplier < 0:
            blockers.append("FEE_UNVERIFIED")
            multiplier = None

    p_yes = _decimal(yes_probability)
    if p_yes is not None and (p_yes < 0 or p_yes > 1):
        p_yes = None
        blockers.append("FORECAST_UNVERIFIED")

    exact_fees = exact_net_fee_dollars if isinstance(exact_net_fee_dollars, dict) else {}
    exact_verified = exact_fee_context_verified is True
    if not exact_verified:
        blockers.append(FIXED_POINT_FEE_BLOCKER)

    for side, level in book["sides"].items():
        price = _price_dollars(level["ask_dollars"])
        qty = _quantity_fp(level["available_contracts_fp"])
        if price is None or qty is None or multiplier is None:
            continue

        base_fee = _quadratic_base_fee_dollars(quantity=qty, price=price, multiplier=multiplier)
        fee_upper = _conservative_net_fee_upper_bound_dollars(
            quantity=qty, price=price, multiplier=multiplier
        )
        exact_fee = _decimal(exact_fees.get(side)) if exact_verified else None
        if exact_verified and (exact_fee is None or exact_fee < 0):
            blockers.append("EXACT_NET_FEE_INVALID")
            exact_fee = None

        probability = None
        theoretical_ev = None
        conservative_after_fee_ev = None
        exact_after_fee_ev = None
        if p_yes is not None:
            probability = p_yes if side == "YES" else Decimal(1) - p_yes
            theoretical_ev = probability * qty - price * qty
            conservative_after_fee_ev = theoretical_ev - fee_upper
            if exact_fee is not None:
                exact_after_fee_ev = theoretical_ev - exact_fee

        rows.append({
            "side": side,
            "ask_dollars": format(price, "f"),
            "available_contracts_fp": format(qty, "f"),
            "book_representation": level["book_representation"],
            "base_quadratic_fee_dollars": format(base_fee, "f"),
            "base_fee_is_exact_net_fee": False,
            "conservative_net_fee_upper_bound_dollars": format(fee_upper, "f"),
            "fee_upper_bound_basis": "MAX_0.01_CONTRACT_FRAGMENTATION_PLUS_CENTICENT_FEE_ROUNDING_PLUS_LT_1C_ACCUMULATOR_RESIDUAL",
            "exact_net_fee_dollars": format(exact_fee, "f") if exact_fee is not None else None,
            "forecast_side_probability": format(probability, "f") if probability is not None else None,
            "pre_fee_model_ev_dollars": format(theoretical_ev, "f") if theoretical_ev is not None else None,
            "conservative_after_fee_model_ev_lower_bound_dollars": (
                format(conservative_after_fee_ev, "f") if conservative_after_fee_ev is not None else None
            ),
            "conservative_after_fee_positive": (
                conservative_after_fee_ev > 0 if conservative_after_fee_ev is not None else None
            ),
            "exact_after_fee_model_ev_dollars": format(exact_after_fee_ev, "f") if exact_after_fee_ev is not None else None,
        })

    blockers = sorted(set(blockers))
    exact_economics_verified = (
        exact_verified
        and "FEE_UNVERIFIED" not in blockers
        and "EXACT_NET_FEE_INVALID" not in blockers
        and bool(rows)
        and all(row["exact_net_fee_dollars"] is not None for row in rows)
    )
    conservative_bounded_economics = (
        "FEE_UNVERIFIED" not in blockers
        and bool(rows)
        and any(row["conservative_after_fee_model_ev_lower_bound_dollars"] is not None for row in rows)
    )

    return {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "book_sides": rows,
        "blockers": blockers,
        "fixed_point_book_verified": not book["blockers"],
        "exact_fee_context_verified": exact_verified,
        "exact_after_fee_economics_verified": exact_economics_verified,
        "conservative_fee_bounded_economics_available": conservative_bounded_economics,
        "economics_status": (
            "EXACT_AFTER_FEE_SHADOW_ECONOMICS"
            if exact_economics_verified
            else "CONSERVATIVE_FEE_BOUNDED_SHADOW_ECONOMICS"
            if conservative_bounded_economics
            else "RESEARCH_ONLY_FEE_ECONOMICS_WITHHELD"
        ),
    }
