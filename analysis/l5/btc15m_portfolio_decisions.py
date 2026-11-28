#!/usr/bin/env python3
"""Research-only portfolio action ranking for paired + directional BTC15m.

No order placement, no live wiring. Probability is an externally calibrated,
as-of estimate. Uncalibrated input explicitly refuses directional promotion.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Inventory:
    up_qty: int
    down_qty: int
    sunk_cost: float
    cash_available: float

@dataclass(frozen=True)
class Candidate:
    action: str
    side: str
    price: float
    qty: int = 1
    execution_cost: float = 0.0
    fill_probability: float = 1.0

def payoff(inv: Inventory, winner_up: bool) -> float:
    return (inv.up_qty if winner_up else inv.down_qty) - inv.sunk_cost

def evaluate(inv: Inventory, candidate: Candidate, p_up: float,
             calibrated: bool, probability_margin: float = 0.0,
             residual_cap: int = 1, max_gross_spend: float = 12.0) -> dict:
    """Compare a candidate to HOLD using full-portfolio terminal payoffs.

    Rejects actions increasing unmatched inventory without calibration.
    Fills are modeled as independent Bernoulli outcomes; real queue/adverse
    selection is not independent, so output is research-only and must be
    replaced by empirical conditional fill probabilities before promotion.
    """
    nums = (inv.sunk_cost, inv.cash_available, p_up, probability_margin,
            candidate.price, candidate.execution_cost, candidate.fill_probability,
            max_gross_spend)
    if not all(math.isfinite(x) for x in nums):
        raise ValueError("nonfinite input")
    if min(inv.up_qty, inv.down_qty) < 0 or min(inv.up_qty, inv.down_qty) != int(min(inv.up_qty, inv.down_qty)):
        raise ValueError("invalid inventory")
    if type(inv.up_qty) is not int or type(inv.down_qty) is not int:
        raise ValueError("integer quantities required")
    if inv.sunk_cost < 0 or inv.cash_available < 0 or max_gross_spend < 0:
        raise ValueError("invalid budget")
    if not 0 <= p_up <= 1 or not 0 <= probability_margin <= 1:
        raise ValueError("invalid probability")
    if candidate.action not in ("HOLD", "BUY") or candidate.side not in ("UP", "DOWN"):
        raise ValueError("invalid action")
    # Split deliberately: bool is a subclass of int, so True would otherwise
    # pass an isinstance check and act as qty=1. The two conditions get
    # distinct messages because they are distinct mistakes, and the inventory
    # guard above already says "integer".
    if type(candidate.qty) is not int:
        raise ValueError("integer quantity required")
    if candidate.qty < 0:
        raise ValueError("invalid quantity")
    if not 0 <= candidate.fill_probability <= 1 or candidate.execution_cost < 0:
        raise ValueError("invalid fill/cost")
    if candidate.action == "BUY" and candidate.qty == 0:
        raise ValueError("BUY quantity must be positive")
    if candidate.action == "BUY" and not 0 < candidate.price < 1:
        raise ValueError("invalid price")
    if candidate.action == "HOLD" and candidate.execution_cost != 0:
        raise ValueError("HOLD cannot incur execution cost")
    if candidate.action == "HOLD" and candidate.qty != 0:
        raise ValueError("HOLD quantity must be zero")
    delta_up = candidate.qty if candidate.action == "BUY" and candidate.side == "UP" else 0
    delta_down = candidate.qty if candidate.action == "BUY" and candidate.side == "DOWN" else 0
    cost = (candidate.price * candidate.qty + candidate.execution_cost) if candidate.action == "BUY" else 0.0
    if cost > inv.cash_available or inv.sunk_cost + cost > max_gross_spend:
        return {"eligible": False, "reason": "BUDGET", "live_eligible": False}
    up, down = inv.up_qty + delta_up, inv.down_qty + delta_down
    if abs(up - down) > residual_cap:
        return {"eligible": False, "reason": "RESIDUAL_CAP", "live_eligible": False}
    unmatched_before = abs(inv.up_qty - inv.down_qty)
    unmatched_after = abs(up - down)
    if unmatched_after > unmatched_before and not calibrated:
        return {"eligible": False, "reason": "UNCALIBRATED_DIRECTIONAL", "live_eligible": False}
    conservative_up = max(0.0, p_up - probability_margin)
    conservative_down = max(0.0, 1.0 - p_up - probability_margin)
    # Conservative per-outcome evaluation of BUY versus HOLD, not a coherent
    # single distribution; pessimistic by construction.
    old_ev = p_up * payoff(inv, True) + (1-p_up) * payoff(inv, False)
    new_up = up - inv.sunk_cost - cost
    new_down = down - inv.sunk_cost - cost
    # Avoid conservative-score bias on sunk costs by computing incremental EV
    # directly: payout on the bought side minus purchase/execution cost.
    side_probability = conservative_up if delta_up else conservative_down if delta_down else 0.0
    delta_ev_if_filled = candidate.qty * side_probability - cost
    expected_increment = candidate.fill_probability * delta_ev_if_filled
    # Risk-reducing completion can be rational even with negative standalone EV:
    # record the guaranteed filled-outcome floor improvement separately.
    old_worst = min(payoff(inv, True), payoff(inv, False))
    worst_case_improvement_if_filled = min(new_up, new_down) - old_worst
    return {"eligible": True, "reason": "RESEARCH_ONLY",
            "hold_expected_payoff": old_ev, "filled_up_payoff": new_up,
            "filled_down_payoff": new_down, "worst_case_filled": min(new_up, new_down),
            "expected_increment_vs_hold": expected_increment,
            "estimated_fill_probability": candidate.fill_probability,
            "candidate_better_than_hold": expected_increment > 0,
            "reduces_unmatched_inventory": unmatched_after < unmatched_before,
            "worst_case_improvement_if_filled": worst_case_improvement_if_filled,
            "live_eligible": False}
