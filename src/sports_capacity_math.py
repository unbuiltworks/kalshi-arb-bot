"""Read-only depth/capital capacity math for certified sports counterparts.

This module contains no network clients and no order-entry code.  It answers the
question the q=1 canaries cannot: how many whole contracts at the currently quoted
price would still satisfy the required after-fee profit floor?

Capacity is deliberately conservative:
- only displayed depth at the supplied price is counted;
- requested tiers must fit on both legs without walking the book;
- venue fees are evaluated at the full tier quantity, preserving fee rounding;
- a tier is never called executable here. Rule/freshness/participation and final
  revalidation remain external gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

D = Decimal
FeeFn = Callable[[Decimal, Decimal], Decimal]
DEFAULT_TIERS = (1, 5, 10, 25, 50, 100)


@dataclass(frozen=True)
class CapacityLeg:
    venue: str
    market_id: str
    side: str
    price: Decimal
    depth: Decimal
    fee_fn: FeeFn


@dataclass(frozen=True)
class CapacityDirection:
    name: str
    settlement_key: str
    legs: tuple[CapacityLeg, CapacityLeg]


def evaluate_direction(
    direction: CapacityDirection,
    *,
    tiers: Iterable[int] = DEFAULT_TIERS,
    min_profit_per_contract: Decimal = D("0.03"),
) -> dict:
    """Evaluate fixed whole-contract tiers against displayed top-level depth."""
    leg1, leg2 = direction.legs
    max_top_depth = min(leg1.depth, leg2.depth)
    rows: list[dict] = []
    best_qualifying: dict | None = None

    for raw_q in tiers:
        q_int = int(raw_q)
        if q_int <= 0:
            raise ValueError("capacity tiers must be positive integers")
        q = D(q_int)
        if q > max_top_depth:
            rows.append({
                "quantity": q_int,
                "status": "DEPTH_BLOCKED",
                "max_top_level_depth": str(max_top_depth),
                "qualifies_profit_floor": False,
            })
            continue

        fee1 = leg1.fee_fn(leg1.price, q)
        fee2 = leg2.fee_fn(leg2.price, q)
        raw_cost = q * (leg1.price + leg2.price)
        net_cost = raw_cost + fee1 + fee2
        locked_profit = q - net_cost
        unit_profit = locked_profit / q
        qualifies = unit_profit >= min_profit_per_contract
        row = {
            "quantity": q_int,
            "status": "QUALIFIES" if qualifies else "EDGE_BLOCKED",
            "qualifies_profit_floor": qualifies,
            "raw_cost": str(raw_cost),
            "fees": str(fee1 + fee2),
            "net_cost": str(net_cost),
            "locked_profit": str(locked_profit),
            "profit_per_contract": str(unit_profit),
            "roi_on_cost": str(locked_profit / net_cost) if net_cost > 0 else "0",
            "capital_by_venue": {
                leg1.venue: str(q * leg1.price + fee1),
                leg2.venue: str(q * leg2.price + fee2),
            },
        }
        rows.append(row)
        if qualifies and (best_qualifying is None or q_int > int(best_qualifying["quantity"])):
            best_qualifying = row

    return {
        "direction": direction.name,
        "settlement_key": direction.settlement_key,
        "legs": [
            {
                "venue": leg.venue,
                "market_id": leg.market_id,
                "side": leg.side,
                "price": str(leg.price),
                "displayed_depth": str(leg.depth),
            }
            for leg in direction.legs
        ],
        "max_top_level_depth": str(max_top_depth),
        "min_profit_per_contract": str(min_profit_per_contract),
        "tiers": rows,
        "largest_qualifying_tier": int(best_qualifying["quantity"]) if best_qualifying else 0,
        "largest_qualifying_locked_profit": best_qualifying["locked_profit"] if best_qualifying else "0",
        "read_only": True,
        "execution_eligible": False,
    }


def summarize_capacity(rows: Iterable[dict]) -> dict:
    """Aggregate the best qualifying top-level tier per independent direction."""
    directions = list(rows)
    qualifying = [row for row in directions if int(row.get("largest_qualifying_tier") or 0) > 0]
    gross_locked = sum((D(str(row.get("largest_qualifying_locked_profit") or "0")) for row in qualifying), D("0"))
    return {
        "directions_evaluated": len(directions),
        "directions_with_3c_capacity": len(qualifying),
        "sum_best_top_level_locked_profit": str(gross_locked),
        "warning": "Snapshot sum is not daily realized capacity and may double-count mutually competing liquidity or repeated observations.",
        "read_only": True,
        "execution_eligible": False,
    }
