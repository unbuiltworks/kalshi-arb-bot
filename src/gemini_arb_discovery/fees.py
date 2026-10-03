"""Gemini prediction-market fees, charged conservatively.

THE SCHEDULE, as established on this repository's Gemini lane and cross-checked
against the account's own ledger in
`analysis/gemini/MAKER_FEE_RESOLVED.md` (branch
`research/gemini-btc5m-information-edge`):

    taker fee = ceil_cent(0.07   x C x P x (1 - P))
    maker fee = ceil_cent(0.0175 x C x P x (1 - P))

P is the contract price in dollars, C the number of contracts. Fees are convex,
maximised at P = 0.50 and vanishing at the boundaries.

WHY THIS SCANNER CHARGES THE TAKER RATE ON EVERY LEG. A discovered arbitrage is
only real if both legs can actually be put on. The only execution that does not
depend on someone else choosing to trade with us is crossing the spread, so the
scanner prices what it can actually guarantee: taker on both legs. Quoting a
maker rate would be pricing a fill we have no right to.

WHY NO REBATE IS EVER CREDITED. The maker rebate and the liquidity-reward
programmes are separate ledgers, conditional, and have been observed to change
without notice on this account. The standing rule on this repository is that
nobody scales on a modeled rebate, so the scanner's net figure contains none.

WHY A ZERO OBSERVED FEE IS NOT USED EITHER. Eighteen maker fills on 2026-09-22
carried $0.00 in both the trade ledger and the fill stream, but sixty earlier
rows on the same account match the maker formula exactly. The zero is real and
it is not a schedule. Charging it would make the scanner's output depend on a
waiver that can lapse between discovery and execution.
"""
from __future__ import annotations

import math
from decimal import Decimal as D
from typing import Union

TAKER_RATE = D("0.07")
MAKER_RATE = D("0.0175")

Num = Union[D, str, int, float]


def ceil_cent(dollars: Num) -> D:
    """Round UP to the next whole cent. Returns dollars.

    The quantum matters: a theoretical $0.001 of fee is charged as $0.01, and at
    arbitrage-sized edges that rounding is frequently the whole result.
    """
    cents = D(str(dollars)) * D("100")
    return D(math.ceil(cents)) / D("100")


def _fee(rate: D, count: Num, price: Num) -> D:
    c, p = D(str(count)), D(str(price))
    if c <= 0 or p <= 0 or p >= 1:
        return D("0")
    return ceil_cent(rate * c * p * (D("1") - p))


def taker_fee(count: Num, price: Num) -> D:
    """Fee in dollars for taking `count` contracts at `price` dollars."""
    return _fee(TAKER_RATE, count, price)


def maker_fee(count: Num, price: Num) -> D:
    """Fee in dollars for making `count` contracts at `price` dollars.

    Provided for completeness and for reporting what a maker-first variant would
    cost. The scanner's net numbers do not use it.
    """
    return _fee(MAKER_RATE, count, price)
