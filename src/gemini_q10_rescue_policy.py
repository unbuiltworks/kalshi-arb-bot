"""Pure safety policy for Gemini q10 V5 bounded rescue.

No network calls and no order entry.  The policy treats rescue capacity as a
standing precondition while maker quotes are live and prices both same-venue
flatten paths after any fill:

* COMPLETE: buy the opposite outcome with a marketable bounded limit;
* UNWIND: sell the filled outcome with a marketable bounded limit.

On Gemini's unified binary top of book the two paths are often economically
identical, but they remain explicit because order-side liquidity/fees can differ
in future API/book representations.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Iterable

from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)

ONE = D("1")
ZERO = D("0")
Q10 = D("10")
DEFAULT_MAX_RESCUE_LOSS_USD = D("0.30")
DEFAULT_MAX_CAMPAIGN_LOSS_USD = D("1.00")
DEFAULT_MAX_DAILY_RISK_DEBIT_USD = D("3.00")
DEFAULT_CALM_GRACE_SECONDS = 2.0
DEFAULT_TOXIC_GRACE_SECONDS = 0.0


@dataclass(frozen=True)
class Top:
    yes_bid: D
    yes_bid_size: D
    yes_ask: D
    yes_ask_size: D

    @property
    def no_bid(self) -> D:
        return ONE - self.yes_ask

    @property
    def no_bid_size(self) -> D:
        return self.yes_ask_size

    @property
    def no_ask(self) -> D:
        return ONE - self.yes_bid

    @property
    def no_ask_size(self) -> D:
        return self.yes_bid_size


@dataclass(frozen=True)
class RescuePath:
    kind: str  # COMPLETE or UNWIND
    side: str  # BUY or SELL
    outcome: str  # YES or NO
    quantity: D
    limit_price: D
    visible_depth: D
    entry_fee: D
    rescue_fee: D
    loss_usd: D

    @property
    def depth_ok(self) -> bool:
        return self.visible_depth >= self.quantity


@dataclass(frozen=True)
class RescuePlan:
    filled_outcome: str
    filled_price: D
    quantity: D
    paths: tuple[RescuePath, ...]

    @property
    def executable_paths(self) -> tuple[RescuePath, ...]:
        return tuple(p for p in self.paths if p.depth_ok)

    @property
    def best(self) -> RescuePath | None:
        choices = self.executable_paths
        if not choices:
            return None
        # Prefer COMPLETE on exact ties: it produces a settlement-neutral pair and
        # avoids depending on sale-position semantics when economics are identical.
        return min(choices, key=lambda p: (p.loss_usd, 0 if p.kind == "COMPLETE" else 1))


def _validate_top(top: Top) -> None:
    if not (ZERO < top.yes_bid < top.yes_ask < ONE):
        raise ValueError("invalid binary top")
    if top.yes_bid_size < ZERO or top.yes_ask_size < ZERO:
        raise ValueError("negative depth")


def _loss_complete(*, filled_outcome: str, filled_price: D, quantity: D, top: Top) -> RescuePath:
    outcome = filled_outcome.upper()
    entry_fee = gemini_predictions_maker_fee(filled_price, quantity)
    if outcome == "YES":
        rescue_outcome = "NO"
        rescue_price = top.no_ask
        depth = top.no_ask_size
    elif outcome == "NO":
        rescue_outcome = "YES"
        rescue_price = top.yes_ask
        depth = top.yes_ask_size
    else:
        raise ValueError("filled_outcome must be YES or NO")
    rescue_fee = gemini_predictions_taker_fee(rescue_price, quantity)
    payout = quantity
    total_cost = filled_price * quantity + rescue_price * quantity + entry_fee + rescue_fee
    loss = max(ZERO, total_cost - payout)
    return RescuePath(
        kind="COMPLETE", side="BUY", outcome=rescue_outcome, quantity=quantity,
        limit_price=rescue_price, visible_depth=depth, entry_fee=entry_fee,
        rescue_fee=rescue_fee, loss_usd=loss,
    )


def _loss_unwind(*, filled_outcome: str, filled_price: D, quantity: D, top: Top) -> RescuePath:
    outcome = filled_outcome.upper()
    entry_fee = gemini_predictions_maker_fee(filled_price, quantity)
    if outcome == "YES":
        sale_price = top.yes_bid
        depth = top.yes_bid_size
    elif outcome == "NO":
        sale_price = top.no_bid
        depth = top.no_bid_size
    else:
        raise ValueError("filled_outcome must be YES or NO")
    rescue_fee = gemini_predictions_taker_fee(sale_price, quantity)
    proceeds = sale_price * quantity
    loss = max(ZERO, filled_price * quantity + entry_fee + rescue_fee - proceeds)
    return RescuePath(
        kind="UNWIND", side="SELL", outcome=outcome, quantity=quantity,
        limit_price=sale_price, visible_depth=depth, entry_fee=entry_fee,
        rescue_fee=rescue_fee, loss_usd=loss,
    )


def rescue_plan(*, filled_outcome: str, filled_price: D, quantity: D, top: Top) -> RescuePlan:
    _validate_top(top)
    qty = D(str(quantity)); px = D(str(filled_price))
    if qty <= ZERO or qty > Q10:
        raise ValueError("rescue quantity must be in (0, 10]")
    if not (ZERO < px < ONE):
        raise ValueError("filled_price must be in (0, 1)")
    paths = (
        _loss_complete(filled_outcome=filled_outcome, filled_price=px, quantity=qty, top=top),
        _loss_unwind(filled_outcome=filled_outcome, filled_price=px, quantity=qty, top=top),
    )
    return RescuePlan(filled_outcome=filled_outcome.upper(), filled_price=px, quantity=qty, paths=paths)


def rescue_is_bounded(*, filled_outcome: str, filled_price: D, quantity: D, top: Top,
                      max_loss_usd: D = DEFAULT_MAX_RESCUE_LOSS_USD) -> bool:
    plan = rescue_plan(filled_outcome=filled_outcome, filled_price=filled_price, quantity=quantity, top=top)
    best = plan.best
    return best is not None and best.loss_usd <= D(str(max_loss_usd))


def standing_rescue_check(*, yes_quote: D, no_quote: D, quantity: D, top: Top,
                          max_loss_usd: D = DEFAULT_MAX_RESCUE_LOSS_USD) -> dict:
    """Verify both possible first-fill states are still bounded right now.

    This is the core V5 invariant.  It must remain true for the entire period any
    maker quote is resting; if either hypothetical first fill can no longer be
    flattened inside the cap, the resting quotes must be cancelled immediately.
    """
    y = rescue_plan(filled_outcome="YES", filled_price=D(str(yes_quote)), quantity=quantity, top=top)
    n = rescue_plan(filled_outcome="NO", filled_price=D(str(no_quote)), quantity=quantity, top=top)
    cap = D(str(max_loss_usd))
    yb, nb = y.best, n.best
    return {
        "safe": bool(yb is not None and nb is not None and yb.loss_usd <= cap and nb.loss_usd <= cap),
        "yes_first_loss_usd": None if yb is None else yb.loss_usd,
        "no_first_loss_usd": None if nb is None else nb.loss_usd,
        "yes_first_path": None if yb is None else yb.kind,
        "no_first_path": None if nb is None else nb.kind,
        "max_loss_usd": cap,
    }


def grace_seconds(*, toxic: bool,
                  calm_seconds: float = DEFAULT_CALM_GRACE_SECONDS,
                  toxic_seconds: float = DEFAULT_TOXIC_GRACE_SECONDS) -> float:
    return max(0.0, float(toxic_seconds if toxic else calm_seconds))


def daily_halt(*, risk_debits: Iterable[D],
               max_daily_risk_debit_usd: D = DEFAULT_MAX_DAILY_RISK_DEBIT_USD) -> bool:
    total = sum((max(ZERO, D(str(x))) for x in risk_debits), ZERO)
    return total >= D(str(max_daily_risk_debit_usd))
