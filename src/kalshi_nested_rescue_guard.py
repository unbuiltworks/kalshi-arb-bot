"""Pure fail-closed rescue-price planning for the first nested live probe.

This module has no network calls.  It converts an operator-supplied maximum
acceptable loss into the *most expensive* hedge price the executor may send for
a filled maker fragment.  It never invents a loss budget: callers must supply
one explicitly.

A rescue limit is not a fill guarantee.  It only caps how far the executor may
cross the hedge book while trying to flatten known maker exposure.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
MIN_Q = D("0.01")
MAX_Q = D("1.00")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")
CENT = D("0.01")


@dataclass(frozen=True)
class RescuePlan:
    quantity: Decimal
    maker_cost: Decimal
    current_hedge_cost: Decimal
    max_rescue_loss: Decimal
    rescue_limit_cost: Decimal
    pnl_at_current_hedge: Decimal
    pnl_at_rescue_limit: Decimal


def _validate_probability_cost(value: Decimal, *, name: str) -> Decimal:
    v = D(value)
    if v < MIN_PRICE or v > MAX_PRICE:
        raise ValueError(f"{name} must be between 0.01 and 0.99")
    return v


def fragment_min_pnl(
    *,
    quantity: Decimal,
    maker_cost: Decimal,
    hedge_cost: Decimal,
    fee_multiplier: Decimal,
) -> Decimal:
    """Conservative min settlement P&L for a maker-filled nested fragment.

    Maker fee is assumed zero only for the first-probe allowed fee type
    (quadratic).  The hedge is charged with the repository's whole-cent ceiling
    model, which intentionally overstates small fractional-fill fees relative to
    accumulated exchange rounding and is therefore suitable as a pre-send cap.
    """
    q = D(quantity)
    if q < MIN_Q or q > MAX_Q:
        raise ValueError("quantity must be between 0.01 and 1.00")
    mc = _validate_probability_cost(D(maker_cost), name="maker_cost")
    hc = _validate_probability_cost(D(hedge_cost), name="hedge_cost")
    multiplier = D(fee_multiplier)
    if multiplier <= 0:
        raise ValueError("fee_multiplier must be positive")
    # Quadratic fees are outcome symmetric, so using hedge economic cost here is
    # equivalent to using the complementary YES-book price.
    fee = kalshi_quadratic_taker_fee(hc, q, fee_multiplier=multiplier)
    return q - q * mc - q * hc - fee


def build_rescue_plan(
    *,
    quantity: Decimal,
    maker_cost: Decimal,
    current_hedge_cost: Decimal,
    fee_multiplier: Decimal,
    max_rescue_loss: Decimal,
) -> RescuePlan:
    """Return the furthest cent price allowed by an explicit loss budget.

    ``max_rescue_loss`` is an absolute dollar amount for this fragment, not a
    per-contract number.  Zero is allowed (never intentionally lock a loss).
    The result is always >= current hedge cost.  If even the current hedge price
    exceeds the supplied loss budget, the function fails closed rather than
    silently widening risk.
    """
    q = D(quantity)
    mc = _validate_probability_cost(D(maker_cost), name="maker_cost")
    current = _validate_probability_cost(D(current_hedge_cost), name="current_hedge_cost")
    multiplier = D(fee_multiplier)
    loss = D(max_rescue_loss)
    if q < MIN_Q or q > MAX_Q:
        raise ValueError("quantity must be between 0.01 and 1.00")
    if multiplier <= 0:
        raise ValueError("fee_multiplier must be positive")
    if loss < 0:
        raise ValueError("max_rescue_loss must be >= 0")
    if loss > D("0.25"):
        raise ValueError("first-probe max_rescue_loss hard ceiling is $0.25")

    current_pnl = fragment_min_pnl(
        quantity=q,
        maker_cost=mc,
        hedge_cost=current,
        fee_multiplier=multiplier,
    )
    if current_pnl < -loss:
        raise ValueError(
            f"current hedge already exceeds rescue-loss budget: pnl={current_pnl} budget={loss}"
        )

    best = current
    best_pnl = current_pnl
    probe = current
    while probe + CENT <= MAX_PRICE:
        candidate = probe + CENT
        pnl = fragment_min_pnl(
            quantity=q,
            maker_cost=mc,
            hedge_cost=candidate,
            fee_multiplier=multiplier,
        )
        if pnl < -loss:
            break
        best = candidate
        best_pnl = pnl
        probe = candidate

    return RescuePlan(
        quantity=q,
        maker_cost=mc,
        current_hedge_cost=current,
        max_rescue_loss=loss,
        rescue_limit_cost=best,
        pnl_at_current_hedge=current_pnl,
        pnl_at_rescue_limit=best_pnl,
    )
