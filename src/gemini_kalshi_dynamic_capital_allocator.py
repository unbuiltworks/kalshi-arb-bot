"""Pure dynamic capital planning for future scaled Gemini/Kalshi crypto execution.

This module contains no network or order-entry code.  It solves the pre-send capital
problem for a Gemini-first complementary pair: compute conservative worst-case cash
required on *each venue* from the exact outbound limit prices and quantity, then reserve
both venue balances atomically before the first leg may be submitted.

The current production q=1/q=5 paths remain untouched.  The larger q=10/q=25 research
path can use this planner only after market-depth/economics gates have independently
qualified a size.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")
MAX_DYNAMIC_QUANTITY = D("25")
DEFAULT_VENUE_BUFFER = D("0.01")


@dataclass(frozen=True)
class CapitalReservationPlan:
    quantity: Decimal
    direction: str
    gemini_price: Decimal
    kalshi_yes_price: Decimal
    kalshi_cost_price: Decimal
    gemini_fee: Decimal
    kalshi_fee: Decimal
    gemini_exact_required: Decimal
    kalshi_exact_required: Decimal
    venue_buffer: Decimal
    gemini_reserved: Decimal
    kalshi_reserved: Decimal

    @property
    def exact_pair_required(self) -> Decimal:
        return self.gemini_exact_required + self.kalshi_exact_required

    @property
    def pair_reserved(self) -> Decimal:
        return self.gemini_reserved + self.kalshi_reserved


def _positive_integer_quantity(value: Any) -> Decimal:
    q = D(str(value))
    if q <= 0 or q != q.to_integral_value() or q > MAX_DYNAMIC_QUANTITY:
        raise ValueError(f"dynamic quantity must be an integer from 1 to {MAX_DYNAMIC_QUANTITY}")
    return q


def _price(value: Any, *, label: str) -> Decimal:
    price = D(str(value))
    if not (MIN_PRICE <= price <= MAX_PRICE):
        raise ValueError(f"{label} must be within 0.01..0.99")
    return price


def plan_exact_pair_reservation(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
    venue_buffer: Decimal = DEFAULT_VENUE_BUFFER,
) -> CapitalReservationPlan:
    """Compute conservative per-venue cash needed at the exact outbound limits.

    Limit-price cash is conservative for both directions used by this strategy:
    Gemini is always BUY; Kalshi ``bid`` buys YES, while Kalshi ``ask`` is the
    complementary buy-NO representation and therefore costs ``1 - yes_price``.
    Fee functions use total-order cent rounding, matching the existing shadow/live
    accounting model.  A small independent venue buffer is added after exact costs.
    """
    if str(gemini_payload.get("side") or "").upper() != "BUY":
        raise ValueError("Gemini scaled capital plan requires BUY")
    if str(gemini_payload.get("type") or "").upper() != "LIMIT":
        raise ValueError("Gemini scaled capital plan requires LIMIT")

    gq = _positive_integer_quantity(gemini_payload.get("quantity"))
    kq = _positive_integer_quantity(kalshi_payload.get("count"))
    if gq != kq:
        raise ValueError(f"venue quantities differ gemini={gq} kalshi={kq}")

    gp = _price(gemini_payload.get("price"), label="Gemini price")
    kp = _price(kalshi_payload.get("price"), label="Kalshi YES price")
    buffer = D(venue_buffer)
    if buffer < 0:
        raise ValueError("venue buffer cannot be negative")
    multiplier = D(kalshi_fee_multiplier)
    if multiplier < 0:
        raise ValueError("Kalshi fee multiplier cannot be negative")

    outcome = str(gemini_payload.get("eventOutcome") or "").upper()
    kside = str(kalshi_payload.get("side") or "").lower()
    if outcome == "NO" and kside == "bid":
        direction = "KALSHI_YES_GEMINI_NO"
        kcost = kp
    elif outcome == "YES" and kside == "ask":
        direction = "GEMINI_YES_KALSHI_NO"
        kcost = D("1") - kp
    else:
        raise ValueError("venue payloads are not complementary supported legs")

    gfee = gemini_predictions_taker_fee(gp, gq)
    kfee = kalshi_quadratic_taker_fee(kp, gq, fee_multiplier=multiplier)
    gexact = gq * gp + gfee
    kexact = gq * kcost + kfee
    return CapitalReservationPlan(
        quantity=gq,
        direction=direction,
        gemini_price=gp,
        kalshi_yes_price=kp,
        kalshi_cost_price=kcost,
        gemini_fee=gfee,
        kalshi_fee=kfee,
        gemini_exact_required=gexact,
        kalshi_exact_required=kexact,
        venue_buffer=buffer,
        gemini_reserved=gexact + buffer,
        kalshi_reserved=kexact + buffer,
    )


def plan_from_capacity_row(
    row: dict[str, Any],
    *,
    venue_buffer: Decimal = DEFAULT_VENUE_BUFFER,
) -> CapitalReservationPlan:
    """Turn a qualified read-only capacity row into a reservation-only plan.

    The row must already be market-supported.  This function deliberately refuses to
    infer support from balances; depth/economics qualification and capital qualification
    stay separate gates.
    """
    if not bool(row.get("supported")):
        raise ValueError("capacity row is not market-supported")
    q = _positive_integer_quantity(row.get("quantity"))
    gexact = D(str(row.get("gemini_capital")))
    kexact = D(str(row.get("kalshi_capital")))
    if gexact <= 0 or kexact <= 0:
        raise ValueError("capacity row venue capital must be positive")
    buffer = D(venue_buffer)
    if buffer < 0:
        raise ValueError("venue buffer cannot be negative")
    direction = str(row.get("direction") or "CAPACITY_ROW")
    return CapitalReservationPlan(
        quantity=q,
        direction=direction,
        gemini_price=D(str(row.get("gemini_price") or "0")),
        kalshi_yes_price=D(str(row.get("kalshi_yes_price") or row.get("cushioned_kalshi_yes_limit") or "0")),
        kalshi_cost_price=D(str(row.get("kalshi_cost_price") or "0")),
        gemini_fee=D(str(row.get("gemini_fee") or "0")),
        kalshi_fee=D(str(row.get("kalshi_fee") or "0")),
        gemini_exact_required=gexact,
        kalshi_exact_required=kexact,
        venue_buffer=buffer,
        gemini_reserved=gexact + buffer,
        kalshi_reserved=kexact + buffer,
    )


class AtomicVenueReservationBook:
    """In-memory proof model: pair reservation mutates neither venue unless both fit."""

    def __init__(self, *, gemini_available: Decimal, kalshi_available: Decimal):
        self.available = {
            "gemini": D(gemini_available),
            "kalshi": D(kalshi_available),
        }
        if self.available["gemini"] < 0 or self.available["kalshi"] < 0:
            raise ValueError("available balances cannot be negative")
        self.reservations: dict[str, CapitalReservationPlan] = {}

    def reserved_for(self, venue: str) -> Decimal:
        venue = str(venue).lower()
        if venue == "gemini":
            return sum((p.gemini_reserved for p in self.reservations.values()), D("0"))
        if venue == "kalshi":
            return sum((p.kalshi_reserved for p in self.reservations.values()), D("0"))
        raise ValueError(f"unknown venue={venue}")

    def free_for(self, venue: str) -> Decimal:
        venue = str(venue).lower()
        return self.available[venue] - self.reserved_for(venue)

    def can_reserve(self, plan: CapitalReservationPlan) -> bool:
        return bool(
            self.free_for("gemini") >= plan.gemini_reserved
            and self.free_for("kalshi") >= plan.kalshi_reserved
        )

    def reserve_pair(self, key: str, plan: CapitalReservationPlan) -> None:
        key = str(key).strip()
        if not key:
            raise ValueError("reservation key is required")
        if key in self.reservations:
            raise ValueError(f"duplicate reservation key={key}")
        # Check both before a single mutation: no one-sided reservation state.
        if not self.can_reserve(plan):
            raise RuntimeError(
                "insufficient pair capital "
                f"gemini_free={self.free_for('gemini')} gemini_need={plan.gemini_reserved} "
                f"kalshi_free={self.free_for('kalshi')} kalshi_need={plan.kalshi_reserved}"
            )
        self.reservations[key] = plan

    def release(self, key: str) -> CapitalReservationPlan:
        if key not in self.reservations:
            raise KeyError(key)
        return self.reservations.pop(key)


def choose_largest_reservable(
    plans: Iterable[CapitalReservationPlan],
    *,
    gemini_free: Decimal,
    kalshi_free: Decimal,
) -> CapitalReservationPlan | None:
    """Choose the largest already-market-supported quantity that both balances fund."""
    gfree = D(gemini_free)
    kfree = D(kalshi_free)
    if gfree < 0 or kfree < 0:
        raise ValueError("free balances cannot be negative")
    ordered = sorted(plans, key=lambda p: p.quantity, reverse=True)
    for plan in ordered:
        if gfree >= plan.gemini_reserved and kfree >= plan.kalshi_reserved:
            return plan
    return None
