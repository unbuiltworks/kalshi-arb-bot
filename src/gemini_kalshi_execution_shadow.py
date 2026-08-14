"""Read-only accounting core for Gemini <-> Kalshi 15-minute crypto shadowing.

This module deliberately contains no network clients and no order-entry path. It
turns synchronized top-of-book snapshots into executable-shadow candidates while
keeping venue fees explicit/configurable and preventing repeated resting depth from
being double-counted as fresh opportunity capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

D = Decimal


@dataclass(frozen=True)
class TopOfBook:
    venue: str
    market_id: str
    ts_ms: int
    yes_bid: Decimal
    yes_bid_size: Decimal
    yes_ask: Decimal
    yes_ask_size: Decimal

    @property
    def no_bid(self) -> Decimal:
        return D("1") - self.yes_ask

    @property
    def no_ask(self) -> Decimal:
        return D("1") - self.yes_bid

    @property
    def no_ask_size(self) -> Decimal:
        # Buying NO crosses the best YES bid. At top-of-book, the displayed size
        # available at that transformed price is the YES-bid size.
        return self.yes_bid_size


FeeFn = Callable[[Decimal, Decimal], Decimal]


def zero_fee(price: Decimal, quantity: Decimal) -> Decimal:
    return D("0")


@dataclass(frozen=True)
class ShadowCandidate:
    direction: str
    quantity: Decimal
    raw_cost: Decimal
    fees: Decimal
    net_cost: Decimal
    locked_profit: Decimal
    roi_on_cost: Decimal
    quote_skew_ms: int


def _candidate(
    *,
    direction: str,
    leg1_price: Decimal,
    leg1_size: Decimal,
    leg2_price: Decimal,
    leg2_size: Decimal,
    leg1_fee: FeeFn,
    leg2_fee: FeeFn,
    quote_skew_ms: int,
    max_quantity: Decimal | None,
) -> ShadowCandidate | None:
    qty = min(leg1_size, leg2_size)
    if max_quantity is not None:
        qty = min(qty, max_quantity)
    if qty <= 0:
        return None

    raw_cost = (leg1_price + leg2_price) * qty
    fees = leg1_fee(leg1_price, qty) + leg2_fee(leg2_price, qty)
    net_cost = raw_cost + fees
    payout = qty
    profit = payout - net_cost
    if profit <= 0 or net_cost <= 0:
        return None
    return ShadowCandidate(
        direction=direction,
        quantity=qty,
        raw_cost=raw_cost,
        fees=fees,
        net_cost=net_cost,
        locked_profit=profit,
        roi_on_cost=profit / net_cost,
        quote_skew_ms=quote_skew_ms,
    )


def evaluate_pair(
    gemini: TopOfBook,
    kalshi: TopOfBook,
    *,
    gemini_fee: FeeFn = zero_fee,
    kalshi_fee: FeeFn = zero_fee,
    max_quote_skew_ms: int = 250,
    max_quantity: Decimal | None = None,
) -> list[ShadowCandidate]:
    """Return positive after-fee cross-venue lock candidates.

    The two complementary directions are:
      1) buy Gemini YES + Kalshi NO
      2) buy Kalshi YES + Gemini NO

    This is only economic shadow math. Settlement-equivalence certification is a
    separate gate because Gemini and Kalshi use different crypto reference indexes.
    """
    skew = abs(gemini.ts_ms - kalshi.ts_ms)
    if skew > max_quote_skew_ms:
        return []

    out: list[ShadowCandidate] = []
    c1 = _candidate(
        direction="GEMINI_YES_KALSHI_NO",
        leg1_price=gemini.yes_ask,
        leg1_size=gemini.yes_ask_size,
        leg2_price=kalshi.no_ask,
        leg2_size=kalshi.no_ask_size,
        leg1_fee=gemini_fee,
        leg2_fee=kalshi_fee,
        quote_skew_ms=skew,
        max_quantity=max_quantity,
    )
    if c1:
        out.append(c1)

    c2 = _candidate(
        direction="KALSHI_YES_GEMINI_NO",
        leg1_price=kalshi.yes_ask,
        leg1_size=kalshi.yes_ask_size,
        leg2_price=gemini.no_ask,
        leg2_size=gemini.no_ask_size,
        leg1_fee=kalshi_fee,
        leg2_fee=gemini_fee,
        quote_skew_ms=skew,
        max_quantity=max_quantity,
    )
    if c2:
        out.append(c2)
    return out


@dataclass
class ConsumableLiquidity:
    """Tracks how much displayed top-level depth has already been counted.

    Repeated observations of the same resting quantity do not create new capacity.
    If displayed quantity grows, only the increment is treated as newly consumable.
    A price change resets the tracked level because it is a different quote level.
    """

    price: Decimal | None = None
    max_seen_size: Decimal = D("0")

    def observe(self, price: Decimal, displayed_size: Decimal) -> Decimal:
        if displayed_size < 0:
            raise ValueError("displayed_size must be non-negative")
        if self.price != price:
            self.price = price
            self.max_seen_size = displayed_size
            return displayed_size
        fresh = max(D("0"), displayed_size - self.max_seen_size)
        if displayed_size > self.max_seen_size:
            self.max_seen_size = displayed_size
        return fresh


def survives(candidate_cost_now: Decimal, candidate_cost_later: Decimal, fees_later: Decimal = D("0")) -> bool:
    """Whether the complementary legs still lock a positive dollar payout later."""
    return candidate_cost_later + fees_later < D("1")
