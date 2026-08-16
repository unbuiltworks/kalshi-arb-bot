"""Bounded hedge construction for a one-contract nested live probe.

Pure helpers only: no network calls and no order placement.  A total rescue-loss
budget is allocated pro-rata across fractional maker fills so multiple fragments
cannot each consume the full probe-level budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.kalshi_nested_live_probe import build_fragment_hedge_payload
from src.kalshi_nested_live_probe_guard import NestedProbePlan
from src.kalshi_nested_rescue_guard import RescuePlan, build_rescue_plan

D = Decimal


@dataclass(frozen=True)
class BoundedHedge:
    payload: dict
    rescue: RescuePlan
    fragment_loss_budget: Decimal


def allocate_fragment_loss_budget(
    *,
    total_probe_loss_budget: Decimal,
    quantity: Decimal,
) -> Decimal:
    total = D(total_probe_loss_budget)
    q = D(quantity)
    if total < 0 or total > D("0.25"):
        raise ValueError("total probe rescue-loss budget must be between $0 and $0.25")
    if q < D("0.01") or q > D("1.00"):
        raise ValueError("fragment quantity must be between 0.01 and 1.00")
    return total * q


def build_bounded_fragment_hedge(
    *,
    plan: NestedProbePlan,
    quantity: Decimal,
    total_probe_loss_budget: Decimal,
    client_order_id: str,
) -> BoundedHedge:
    """Build a FOK hedge at the furthest price allowed by the probe loss cap.

    The starting hedge cost is the cost that was validated immediately before the
    maker was posted.  The rescue planner may widen that price only as far as the
    pro-rata fragment loss budget permits.  The resulting FOK can still fail if
    there is insufficient liquidity at/below the bounded limit.
    """
    q = D(quantity)
    fragment_budget = allocate_fragment_loss_budget(
        total_probe_loss_budget=total_probe_loss_budget,
        quantity=q,
    )
    rescue = build_rescue_plan(
        quantity=q,
        maker_cost=plan.maker.cost,
        current_hedge_cost=plan.hedge.cost,
        fee_multiplier=plan.fee_multiplier,
        max_rescue_loss=fragment_budget,
    )
    payload = build_fragment_hedge_payload(
        base_leg=plan.hedge,
        quantity=q,
        live_cost=rescue.rescue_limit_cost,
        client_order_id=client_order_id,
    )
    if payload.get("time_in_force") != "fill_or_kill" or payload.get("post_only"):
        raise RuntimeError("bounded rescue hedge must remain non-post-only FOK")
    return BoundedHedge(
        payload=payload,
        rescue=rescue,
        fragment_loss_budget=fragment_budget,
    )
