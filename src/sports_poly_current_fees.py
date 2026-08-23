"""Current Polymarket US trading-fee helpers for sports execution research.

Official exchange-wide schedule effective 2026-07-01:
- taker theta: 0.06
- maker rebate theta: 0.0125
- both rounded to nearest cent using banker's rounding (half-even)

Maker rebate is returned as a positive credit amount.  These helpers deliberately
ignore volume-tier taker rebates and incentive programs so locked-profit estimates
do not depend on discretionary/retrospective rewards.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN

D = Decimal
ONE = D("1")
CENT = D("0.01")
TAKER_THETA = D("0.06")
MAKER_REBATE_THETA = D("0.0125")


def _rounded(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


def taker_fee(quantity: Decimal | int, price: Decimal) -> Decimal:
    q = D(str(quantity))
    if q <= 0 or price <= 0 or price >= ONE:
        return D("0")
    return _rounded(TAKER_THETA * q * price * (ONE - price))


def maker_rebate(quantity: Decimal | int, price: Decimal) -> Decimal:
    q = D(str(quantity))
    if q <= 0 or price <= 0 or price >= ONE:
        return D("0")
    return _rounded(MAKER_REBATE_THETA * q * price * (ONE - price))


def maker_then_taker_locked_profit(*, maker_price: Decimal, taker_price: Decimal, quantity: Decimal | int = 1) -> dict[str, Decimal]:
    q = D(str(quantity))
    rebate = maker_rebate(q, maker_price)
    fee = taker_fee(q, taker_price)
    payout = q
    cost = q * (maker_price + taker_price) + fee - rebate
    return {
        "maker_rebate": rebate,
        "taker_fee": fee,
        "all_in_cost": cost,
        "locked_profit": payout - cost,
    }
