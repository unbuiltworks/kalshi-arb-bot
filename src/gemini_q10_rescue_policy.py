"""Pure safety policy for Gemini q10 first-fill rescue.

This module contains no network calls and no order entry. It prices both ways to
remove a one-sided prediction-market position on Gemini:

1) COMPLETE: buy the complementary outcome and lock the $1 payout.
2) UNWIND: sell the filled outcome and return to flat immediately.

For Gemini's binary YES book the two routes cross the same economic side of the
book, so gross economics are normally identical. We still price both explicitly
because the executor should make that choice rather than assume it.

The important invariant is a standing one: while a q10 maker quote is resting,
there must be enough current top-level depth to flatten the full possible first fill
inside a hard total-dollar loss budget. Maker rebates and liquidity rewards are
intentionally excluded from the safety calculation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal as D
from typing import Any

from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)

ZERO = D("0")
ONE = D("1")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")


@dataclass(frozen=True)
class RescueRoute:
    action: str
    side: str
    outcome: str
    quantity: D
    current_execution_price: D
    limit_price: D
    available_top_quantity: D
    pnl_at_current: D
    pnl_at_limit: D
    loss_at_current: D
    loss_at_limit: D
    depth_ok: bool
    safe: bool

    def as_dict(self) -> dict[str, Any]:
        return {k: str(v) if isinstance(v, D) else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class StandingRescueCheck:
    safe: bool
    yes_fill_route: RescueRoute
    no_fill_route: RescueRoute
    worst_current_loss: D
    max_loss: D

    def as_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "yes_fill_route": self.yes_fill_route.as_dict(),
            "no_fill_route": self.no_fill_route.as_dict(),
            "worst_current_loss": str(self.worst_current_loss),
            "max_loss": str(self.max_loss),
        }


def _validate_price(price: D) -> D:
    value = D(str(price))
    if not (ZERO < value < ONE):
        raise ValueError("price must be strictly between 0 and 1")
    return value


def _validate_quantity(quantity: D) -> D:
    value = D(str(quantity))
    if value <= ZERO:
        raise ValueError("quantity must be positive")
    return value


def _ticks(increment: D) -> list[D]:
    inc = D(str(increment))
    if inc <= ZERO:
        raise ValueError("increment must be positive")
    out: list[D] = []
    p = MIN_PRICE
    while p <= MAX_PRICE + D("0.0000001"):
        out.append(p)
        p += inc
    return out


def complete_pair_pnl(*, filled_price: D, complement_price: D, quantity: D) -> D:
    """Locked P&L after maker first fill + taker complementary BUY."""
    p = _validate_price(filled_price)
    c = _validate_price(complement_price)
    q = _validate_quantity(quantity)
    maker_fee = gemini_predictions_maker_fee(p, q)
    taker_fee = gemini_predictions_taker_fee(c, q)
    return q - (p * q) - (c * q) - maker_fee - taker_fee


def unwind_pnl(*, filled_price: D, unwind_price: D, quantity: D) -> D:
    """P&L after maker first fill + taker SELL of the same outcome."""
    p = _validate_price(filled_price)
    u = _validate_price(unwind_price)
    q = _validate_quantity(quantity)
    maker_fee = gemini_predictions_maker_fee(p, q)
    taker_fee = gemini_predictions_taker_fee(u, q)
    return (u * q) - (p * q) - maker_fee - taker_fee


def _loss(pnl: D) -> D:
    return max(ZERO, -D(str(pnl)))


def max_complete_price(*, filled_price: D, quantity: D, max_loss: D,
                       increment: D = D("0.01")) -> D | None:
    """Highest BUY limit whose worst fill still respects the total loss bound."""
    bound = D(str(max_loss))
    if bound < ZERO:
        raise ValueError("max_loss cannot be negative")
    safe: D | None = None
    for p in _ticks(increment):
        if complete_pair_pnl(filled_price=filled_price, complement_price=p, quantity=quantity) >= -bound:
            safe = p
    return safe


def min_unwind_price(*, filled_price: D, quantity: D, max_loss: D,
                     increment: D = D("0.01")) -> D | None:
    """Lowest SELL limit whose worst fill still respects the total loss bound."""
    bound = D(str(max_loss))
    if bound < ZERO:
        raise ValueError("max_loss cannot be negative")
    for p in _ticks(increment):
        if unwind_pnl(filled_price=filled_price, unwind_price=p, quantity=quantity) >= -bound:
            return p
    return None


def routes_after_fill(*, filled_outcome: str, filled_price: D, quantity: D,
                      yes_bid: D, yes_bid_size: D, yes_ask: D, yes_ask_size: D,
                      max_loss: D, increment: D = D("0.01")) -> tuple[RescueRoute, RescueRoute]:
    """Price COMPLETE and UNWIND using the current Gemini YES top of book."""
    outcome = str(filled_outcome).upper()
    if outcome not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    q = _validate_quantity(quantity)
    bid = _validate_price(yes_bid)
    ask = _validate_price(yes_ask)
    if bid >= ask:
        raise ValueError("crossed/locked book")
    bid_size = D(str(yes_bid_size))
    ask_size = D(str(yes_ask_size))
    if bid_size < ZERO or ask_size < ZERO:
        raise ValueError("negative depth")
    bound = D(str(max_loss))

    if outcome == "YES":
        missing = "NO"
        # Buying NO crosses the displayed YES bid. Selling YES crosses the same bid.
        complete_current = ONE - bid
        unwind_current = bid
        depth = bid_size
    else:
        missing = "YES"
        # Buying YES crosses the displayed YES ask. Selling NO crosses the transformed NO bid.
        complete_current = ask
        unwind_current = ONE - ask
        depth = ask_size

    complete_limit = max_complete_price(
        filled_price=filled_price, quantity=q, max_loss=bound, increment=increment,
    )
    unwind_limit = min_unwind_price(
        filled_price=filled_price, quantity=q, max_loss=bound, increment=increment,
    )

    complete_pnl_now = complete_pair_pnl(
        filled_price=filled_price, complement_price=complete_current, quantity=q,
    )
    unwind_pnl_now = unwind_pnl(
        filled_price=filled_price, unwind_price=unwind_current, quantity=q,
    )
    depth_ok = depth >= q

    complete_safe = bool(
        complete_limit is not None
        and complete_current <= complete_limit
        and depth_ok
        and _loss(complete_pnl_now) <= bound
    )
    unwind_safe = bool(
        unwind_limit is not None
        and unwind_current >= unwind_limit
        and depth_ok
        and _loss(unwind_pnl_now) <= bound
    )

    complete_limit_value = complete_limit if complete_limit is not None else MAX_PRICE
    unwind_limit_value = unwind_limit if unwind_limit is not None else MIN_PRICE
    complete = RescueRoute(
        action="COMPLETE",
        side="BUY",
        outcome=missing,
        quantity=q,
        current_execution_price=complete_current,
        limit_price=complete_limit_value,
        available_top_quantity=depth,
        pnl_at_current=complete_pnl_now,
        pnl_at_limit=complete_pair_pnl(
            filled_price=filled_price, complement_price=complete_limit_value, quantity=q,
        ),
        loss_at_current=_loss(complete_pnl_now),
        loss_at_limit=_loss(complete_pair_pnl(
            filled_price=filled_price, complement_price=complete_limit_value, quantity=q,
        )),
        depth_ok=depth_ok,
        safe=complete_safe,
    )
    unwind = RescueRoute(
        action="UNWIND",
        side="SELL",
        outcome=outcome,
        quantity=q,
        current_execution_price=unwind_current,
        limit_price=unwind_limit_value,
        available_top_quantity=depth,
        pnl_at_current=unwind_pnl_now,
        pnl_at_limit=unwind_pnl(
            filled_price=filled_price, unwind_price=unwind_limit_value, quantity=q,
        ),
        loss_at_current=_loss(unwind_pnl_now),
        loss_at_limit=_loss(unwind_pnl(
            filled_price=filled_price, unwind_price=unwind_limit_value, quantity=q,
        )),
        depth_ok=depth_ok,
        safe=unwind_safe,
    )
    return complete, unwind


def best_route_after_fill(**kwargs: Any) -> RescueRoute:
    """Return the safest current route; prefer immediate flatness on an economic tie."""
    complete, unwind = routes_after_fill(**kwargs)
    safe = [route for route in (complete, unwind) if route.safe]
    if not safe:
        # Return the economically better current route for diagnostics, but it remains unsafe.
        return max((complete, unwind), key=lambda r: (r.pnl_at_current, r.action == "UNWIND"))
    return max(safe, key=lambda r: (r.pnl_at_current, r.action == "UNWIND"))


def standing_rescue_check(*, yes_quote: D, no_quote: D, quantity: D,
                          yes_bid: D, yes_bid_size: D, yes_ask: D, yes_ask_size: D,
                          max_loss: D, increment: D = D("0.01")) -> StandingRescueCheck:
    """Require both possible q10 first fills to be flattenable *right now*.

    This check is intended to run continuously while either maker quote is live.
    A false result means cancel the resting quotes before accepting more first-fill risk.
    """
    yes_route = best_route_after_fill(
        filled_outcome="YES", filled_price=yes_quote, quantity=quantity,
        yes_bid=yes_bid, yes_bid_size=yes_bid_size, yes_ask=yes_ask, yes_ask_size=yes_ask_size,
        max_loss=max_loss, increment=increment,
    )
    no_route = best_route_after_fill(
        filled_outcome="NO", filled_price=no_quote, quantity=quantity,
        yes_bid=yes_bid, yes_bid_size=yes_bid_size, yes_ask=yes_ask, yes_ask_size=yes_ask_size,
        max_loss=max_loss, increment=increment,
    )
    worst = max(yes_route.loss_at_current, no_route.loss_at_current)
    return StandingRescueCheck(
        safe=bool(yes_route.safe and no_route.safe),
        yes_fill_route=yes_route,
        no_fill_route=no_route,
        worst_current_loss=worst,
        max_loss=D(str(max_loss)),
    )


def maker_grace_seconds(*, spot_dangerous: bool, calm_grace_seconds: float = 1.5) -> float:
    """Toxic first fills get zero maker grace; calm fills may get a tiny bounded grace."""
    if spot_dangerous:
        return 0.0
    return max(0.0, float(calm_grace_seconds))
