"""
Kalshi fee calculator.

Fee formula (from official fee schedule, effective Feb 5, 2026):
  taker_fee = ceil(rate × C × P × (1 - P))  where rate = 0.07 (standard) or 0.035 (INX/NASDAQ100)
  maker_fee = ceil(0.0175 × C × P × (1 - P))

P = price in dollars (50 cents = 0.50)
C = number of contracts
ceil = round UP to next cent (i.e., $0.001 becomes $0.01)

Fees are convex: maximized at P=0.50, zero at P=0 and P=1.
"""

import math
from config.settings import (
    TAKER_FEE_RATE,
    MAKER_FEE_RATE,
    REDUCED_TAKER_FEE_RATE,
    REDUCED_FEE_PREFIXES,
)


def _ceil_cents(dollars: float) -> int:
    """Round up to next cent, return as integer cents.
    
    Uses round() first to eliminate floating-point noise (e.g., 0.63 represented
    as 0.6300000000000001) before applying ceil. Without this, ceil would bump
    exact-cent values up by 1.
    """
    cents = round(dollars * 100, 6)  # kill FP noise, keep real fractions
    return math.ceil(cents)


def _fee_dollars(rate: float, count: int, price_cents: int) -> float:
    """Raw fee in dollars (before rounding)."""
    p = price_cents / 100.0  # convert cents to dollar fraction
    return rate * count * p * (1.0 - p)


def taker_fee_cents(count: int, price_cents: int, ticker: str = "") -> int:
    """
    Taker fee in cents (rounded up).

    Args:
        count: number of contracts
        price_cents: price per contract in cents (1-99)
        ticker: market ticker, used to check for reduced-fee markets

    Returns:
        fee in cents (integer, rounded up)
    """
    if count <= 0 or price_cents <= 0 or price_cents >= 100:
        return 0

    rate = TAKER_FEE_RATE
    for prefix in REDUCED_FEE_PREFIXES:
        if ticker.upper().startswith(prefix):
            rate = REDUCED_TAKER_FEE_RATE
            break

    return _ceil_cents(_fee_dollars(rate, count, price_cents))


def maker_fee_cents(count: int, price_cents: int) -> int:
    """
    Maker fee in cents (rounded up).

    Args:
        count: number of contracts
        price_cents: price per contract in cents (1-99)

    Returns:
        fee in cents (integer, rounded up)
    """
    if count <= 0 or price_cents <= 0 or price_cents >= 100:
        return 0

    return _ceil_cents(_fee_dollars(MAKER_FEE_RATE, count, price_cents))


def total_arb_fees_cents(
    count: int,
    yes_price_cents: int,
    no_price_cents: int,
    ticker: str = "",
    maker_side: str = "yes",
) -> int:
    """
    Total fees for an arb trade (one maker leg + one taker leg).

    Args:
        count: contracts per side
        yes_price_cents: price to buy YES (cents)
        no_price_cents: price to buy NO (cents)
        ticker: market ticker
        maker_side: which side is the maker leg ("yes" or "no")

    Returns:
        total fee in cents for both legs
    """
    if maker_side == "yes":
        yes_fee = maker_fee_cents(count, yes_price_cents)
        no_fee = taker_fee_cents(count, no_price_cents, ticker)
    else:
        yes_fee = taker_fee_cents(count, yes_price_cents, ticker)
        no_fee = maker_fee_cents(count, no_price_cents)

    return yes_fee + no_fee


def compute_edge_cents(
    count: int,
    yes_ask_cents: int,
    no_ask_cents: int,
    ticker: str = "",
    maker_side: str = "yes",
) -> int:
    """
    Compute the arb edge in cents (profit per set of paired contracts).

    Guaranteed payout = 100 × count (since one side settles at $1)
    Total cost = (yes_ask × count) + (no_ask × count) + fees

    Returns:
        edge in cents (positive = profitable arb)
    """
    payout = 100 * count
    cost = (yes_ask_cents * count) + (no_ask_cents * count)
    fees = total_arb_fees_cents(count, yes_ask_cents, no_ask_cents, ticker, maker_side)
    return payout - cost - fees
