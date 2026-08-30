"""Pure adaptive maker-entry policy for Gemini BTC5 V5.3.

No network calls and no order entry. The policy chooses the largest q=1..10 clip
that can be quoted MOC/maker-only while preserving both:
- >=5c per contract completed-pair profit after conservative maker fees; and
- <=$0.30 absolute loss if the full first maker leg must be rescued immediately.

Liquidity rewards are not modeled. Maker rebates are also excluded from modeled
profit and the 20c..80c rebate-eligibility band is reported as metadata only.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

from src import gemini_q10_rescue_policy as rescue
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot

ZERO = D("0")
ONE = D("1")
DEFAULT_MAX_Q = 10
DEFAULT_MIN_Q = 1
DEFAULT_MAX_LOSS = D("0.30")
DEFAULT_EDGE_PER_CONTRACT = D("0.05")
REBATE_MIN = D("0.20")
REBATE_MAX = D("0.80")


def pair_profit(*, yes_quote: D, no_quote: D, quantity: D) -> D:
    yes = D(str(yes_quote))
    no = D(str(no_quote))
    q = D(str(quantity))
    return (
        q * (ONE - yes - no)
        - gemini_predictions_maker_fee(yes, q)
        - gemini_predictions_maker_fee(no, q)
    )


def rebate_eligible(price: D) -> bool:
    return REBATE_MIN <= D(str(price)) <= REBATE_MAX


def _grid(lo: D, hi: D, inc: D):
    value = D(str(lo))
    high = D(str(hi))
    step = D(str(inc))
    if step <= ZERO:
        raise ValueError("price increment must be positive")
    while value <= high + D("0.0000001"):
        yield value
        value += step


@dataclass(frozen=True)
class AdaptiveMakerPlan:
    quantity: D
    yes_quote: D
    no_quote: D
    pair_profit: D
    edge_floor: D
    worst_rescue_loss: D
    yes_fill_route: rescue.RescueRoute
    no_fill_route: rescue.RescueRoute
    yes_rebate_eligible: bool
    no_rebate_eligible: bool
    yes_improvement: D
    no_improvement: D

    @property
    def both_rebate_eligible(self) -> bool:
        return self.yes_rebate_eligible and self.no_rebate_eligible

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity": str(self.quantity),
            "yes_quote": str(self.yes_quote),
            "no_quote": str(self.no_quote),
            "pair_profit": str(self.pair_profit),
            "edge_floor": str(self.edge_floor),
            "worst_rescue_loss": str(self.worst_rescue_loss),
            "yes_fill_route": self.yes_fill_route.as_dict(),
            "no_fill_route": self.no_fill_route.as_dict(),
            "yes_rebate_eligible": self.yes_rebate_eligible,
            "no_rebate_eligible": self.no_rebate_eligible,
            "both_rebate_eligible": self.both_rebate_eligible,
            "rebates_modeled_in_profit": False,
            "liquidity_rewards_modeled": False,
            "yes_improvement": str(self.yes_improvement),
            "no_improvement": str(self.no_improvement),
        }


def _safe_routes_for_side(
    *,
    outcome: str,
    prices: list[D],
    quantity: D,
    snap: DepthSnapshot,
    max_loss: D,
    increment: D,
) -> dict[D, rescue.RescueRoute]:
    result: dict[D, rescue.RescueRoute] = {}
    for price in prices:
        route = rescue.best_route_after_fill(
            filled_outcome=outcome,
            filled_price=price,
            quantity=quantity,
            yes_bid=snap.bid,
            yes_bid_size=snap.bid_size,
            yes_ask=snap.ask,
            yes_ask_size=snap.ask_size,
            max_loss=max_loss,
            increment=increment,
        )
        if route.safe:
            result[price] = route
    return result


def plan_for_quantity(
    *,
    quantity: D,
    snap: DepthSnapshot,
    increment: D,
    max_loss: D = DEFAULT_MAX_LOSS,
    edge_per_contract: D = DEFAULT_EDGE_PER_CONTRACT,
) -> AdaptiveMakerPlan | None:
    q = D(str(quantity))
    inc = D(str(increment))
    if q <= ZERO:
        raise ValueError("quantity must be positive")

    # BUY MOC may rest at the current bid but must remain at least one tick below
    # the opposite top. NO prices are the complement transform of the YES book.
    yes_lo = max(inc, snap.bid)
    yes_hi = min(ONE - inc, snap.ask - inc)
    no_lo = max(inc, ONE - snap.ask)
    no_hi = min(ONE - inc, (ONE - snap.bid) - inc)
    if yes_lo > yes_hi or no_lo > no_hi:
        return None

    yes_prices = list(_grid(yes_lo, yes_hi, inc))
    no_prices = list(_grid(no_lo, no_hi, inc))
    yes_safe = _safe_routes_for_side(
        outcome="YES", prices=yes_prices, quantity=q, snap=snap,
        max_loss=D(str(max_loss)), increment=inc,
    )
    no_safe = _safe_routes_for_side(
        outcome="NO", prices=no_prices, quantity=q, snap=snap,
        max_loss=D(str(max_loss)), increment=inc,
    )
    if not yes_safe or not no_safe:
        return None

    floor = D(str(edge_per_contract)) * q
    best: tuple[tuple[Any, ...], AdaptiveMakerPlan] | None = None
    for yes, yes_route in yes_safe.items():
        for no, no_route in no_safe.items():
            profit = pair_profit(yes_quote=yes, no_quote=no, quantity=q)
            if profit < floor:
                continue
            yes_improve = yes - yes_lo
            no_improve = no - no_lo
            plan = AdaptiveMakerPlan(
                quantity=q,
                yes_quote=yes,
                no_quote=no,
                pair_profit=profit,
                edge_floor=floor,
                worst_rescue_loss=max(yes_route.loss_at_current, no_route.loss_at_current),
                yes_fill_route=yes_route,
                no_fill_route=no_route,
                yes_rebate_eligible=rebate_eligible(yes),
                no_rebate_eligible=rebate_eligible(no),
                yes_improvement=yes_improve,
                no_improvement=no_improve,
            )
            # Use excess edge to maximize fillability, then favor balanced movement.
            # Rebate eligibility is only a final tie-break because rebate dollars are
            # excluded from the safety/profit model.
            score = (
                yes + no,
                min(yes_improve, no_improve),
                yes_improve + no_improve,
                int(plan.both_rebate_eligible),
                yes,
            )
            if best is None or score > best[0]:
                best = (score, plan)
    return None if best is None else best[1]


def select_plan(
    *,
    snap: DepthSnapshot,
    increment: D,
    min_q: int = DEFAULT_MIN_Q,
    max_q: int = DEFAULT_MAX_Q,
    max_loss: D = DEFAULT_MAX_LOSS,
    edge_per_contract: D = DEFAULT_EDGE_PER_CONTRACT,
) -> AdaptiveMakerPlan | None:
    if min_q < 1 or max_q < min_q or max_q > DEFAULT_MAX_Q:
        raise ValueError("adaptive quantity bounds must satisfy 1 <= min_q <= max_q <= 10")
    for q in range(max_q, min_q - 1, -1):
        plan = plan_for_quantity(
            quantity=D(q),
            snap=snap,
            increment=increment,
            max_loss=max_loss,
            edge_per_contract=edge_per_contract,
        )
        if plan is not None:
            return plan
    return None


def standing_rescue_for_plan(
    *,
    plan: AdaptiveMakerPlan,
    snap: DepthSnapshot,
    increment: D,
    yes_resting: bool,
    no_resting: bool,
    max_loss: D = DEFAULT_MAX_LOSS,
) -> rescue.StandingRescueCheck | None:
    """Revalidate a live plan against external depth after removing owned quotes."""
    external = snap.externalized(
        own_yes_price=plan.yes_quote if yes_resting else None,
        own_yes_quantity=plan.quantity if yes_resting else ZERO,
        own_no_price=plan.no_quote if no_resting else None,
        own_no_quantity=plan.quantity if no_resting else ZERO,
    )
    if external is None:
        return None
    return rescue.standing_rescue_check(
        yes_quote=plan.yes_quote,
        no_quote=plan.no_quote,
        quantity=plan.quantity,
        yes_bid=external.bid,
        yes_bid_size=external.bid_size,
        yes_ask=external.ask,
        yes_ask_size=external.ask_size,
        max_loss=D(str(max_loss)),
        increment=D(str(increment)),
    )
