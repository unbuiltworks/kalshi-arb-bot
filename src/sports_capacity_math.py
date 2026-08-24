"""Read-only depth/capital capacity math for certified sports counterparts.

This module contains no network clients and no order-entry code. It answers the
question the q=1 canaries cannot: how many whole contracts at the currently quoted
price would still satisfy the required after-fee profit floor?

Capacity is deliberately conservative:
- only displayed depth at the supplied price is counted;
- requested tiers must fit on both legs without walking the book;
- venue fees are evaluated at the full tier quantity, preserving fee rounding;
- optional venue balances are reduced by explicit free-cash buffers before sizing;
- a tier is never called executable here. Rule/freshness/participation and final
  revalidation remain external gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, Mapping

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


def _capital_requirements(leg1: CapacityLeg, leg2: CapacityLeg, q: Decimal, fee1: Decimal, fee2: Decimal) -> dict[str, Decimal]:
    required: dict[str, Decimal] = {}
    for leg, fee in ((leg1, fee1), (leg2, fee2)):
        required[leg.venue] = required.get(leg.venue, D("0")) + q * leg.price + fee
    return required


def _usable_capital(
    venue: str,
    available_by_venue: Mapping[str, Decimal] | None,
    free_buffer_by_venue: Mapping[str, Decimal] | None,
    default_free_buffer: Decimal,
) -> Decimal | None:
    if available_by_venue is None or venue not in available_by_venue:
        return None
    available = max(D("0"), D(str(available_by_venue[venue])))
    if free_buffer_by_venue is not None and venue in free_buffer_by_venue:
        buffer = max(D("0"), D(str(free_buffer_by_venue[venue])))
    else:
        buffer = max(D("0"), D(str(default_free_buffer)))
    return max(D("0"), available - buffer)


def evaluate_direction(
    direction: CapacityDirection,
    *,
    tiers: Iterable[int] = DEFAULT_TIERS,
    min_profit_per_contract: Decimal = D("0.03"),
    available_by_venue: Mapping[str, Decimal] | None = None,
    free_buffer_by_venue: Mapping[str, Decimal] | None = None,
    default_free_buffer: Decimal = D("0"),
) -> dict:
    """Evaluate fixed whole-contract tiers against displayed depth and capital.

    With no venue balances supplied, ``largest_qualifying_tier`` is economic/depth
    capacity only. With balances supplied, it is the largest tier that also fits
    the specified per-venue usable capital. The economic tier remains separately
    exposed so balance shortfalls can be measured instead of hiding opportunity.
    """
    leg1, leg2 = direction.legs
    max_top_depth = min(leg1.depth, leg2.depth)
    rows: list[dict] = []
    best_economic: dict | None = None
    best_fundable: dict | None = None

    venues = sorted({leg1.venue, leg2.venue})
    usable_by_venue = {
        venue: _usable_capital(
            venue,
            available_by_venue,
            free_buffer_by_venue,
            default_free_buffer,
        )
        for venue in venues
    }
    balances_supplied = available_by_venue is not None and all(usable_by_venue[v] is not None for v in venues)

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
                "economic_profit_floor_pass": False,
                "capital_sufficient": False if balances_supplied else None,
                "qualifies_profit_floor": False,
            })
            continue

        fee1 = leg1.fee_fn(leg1.price, q)
        fee2 = leg2.fee_fn(leg2.price, q)
        raw_cost = q * (leg1.price + leg2.price)
        net_cost = raw_cost + fee1 + fee2
        locked_profit = q - net_cost
        unit_profit = locked_profit / q
        economic_pass = unit_profit >= min_profit_per_contract
        capital_required = _capital_requirements(leg1, leg2, q, fee1, fee2)
        capital_sufficient = None
        capital_blockers: list[str] = []
        if balances_supplied:
            capital_sufficient = True
            for venue, required in capital_required.items():
                usable = usable_by_venue[venue]
                assert usable is not None
                if usable < required:
                    capital_sufficient = False
                    capital_blockers.append(venue)

        if not economic_pass:
            status = "EDGE_BLOCKED"
        elif capital_sufficient is False:
            status = "CAPITAL_BLOCKED"
        else:
            status = "QUALIFIES"

        row = {
            "quantity": q_int,
            "status": status,
            "qualifies_profit_floor": economic_pass,
            "economic_profit_floor_pass": economic_pass,
            "capital_sufficient": capital_sufficient,
            "capital_blockers": capital_blockers,
            "raw_cost": str(raw_cost),
            "fees": str(fee1 + fee2),
            "net_cost": str(net_cost),
            "locked_profit": str(locked_profit),
            "profit_per_contract": str(unit_profit),
            "roi_on_cost": str(locked_profit / net_cost) if net_cost > 0 else "0",
            "capital_by_venue": {venue: str(value) for venue, value in capital_required.items()},
            "usable_capital_by_venue": {
                venue: (str(usable_by_venue[venue]) if usable_by_venue[venue] is not None else None)
                for venue in venues
            },
        }
        rows.append(row)
        if economic_pass and (best_economic is None or q_int > int(best_economic["quantity"])):
            best_economic = row
        if status == "QUALIFIES" and (best_fundable is None or q_int > int(best_fundable["quantity"])):
            best_fundable = row

    effective = best_fundable if balances_supplied else best_economic
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
        "balances_supplied": balances_supplied,
        "usable_capital_by_venue": {
            venue: (str(usable_by_venue[venue]) if usable_by_venue[venue] is not None else None)
            for venue in venues
        },
        "tiers": rows,
        "largest_economic_tier": int(best_economic["quantity"]) if best_economic else 0,
        "largest_economic_locked_profit": best_economic["locked_profit"] if best_economic else "0",
        "largest_fundable_tier": int(best_fundable["quantity"]) if best_fundable else 0,
        "largest_fundable_locked_profit": best_fundable["locked_profit"] if best_fundable else "0",
        "largest_qualifying_tier": int(effective["quantity"]) if effective else 0,
        "largest_qualifying_locked_profit": effective["locked_profit"] if effective else "0",
        "read_only": True,
        "execution_eligible": False,
    }


def summarize_capacity(rows: Iterable[dict]) -> dict:
    """Aggregate the best qualifying top-level tier per independent direction."""
    directions = list(rows)
    qualifying = [row for row in directions if int(row.get("largest_qualifying_tier") or 0) > 0]
    gross_locked = sum((D(str(row.get("largest_qualifying_locked_profit") or "0")) for row in qualifying), D("0"))
    blockers: dict[str, int] = {}
    for row in directions:
        for tier in row.get("tiers") or []:
            status = str(tier.get("status") or "UNKNOWN")
            blockers[status] = blockers.get(status, 0) + 1
    return {
        "directions_evaluated": len(directions),
        "directions_with_3c_capacity": len(qualifying),
        "sum_best_top_level_locked_profit": str(gross_locked),
        "tier_status_counts": blockers,
        "warning": "Snapshot sum is not daily realized capacity and may double-count mutually competing liquidity or repeated observations.",
        "read_only": True,
        "execution_eligible": False,
    }
