"""Fail-closed quantity graduation policy for sports arbitrage.

This module does not submit orders and does not authorize live quantity by itself.
It converts read-only capacity telemetry into a proposed tier only when a caller has
already supplied every external safety gate and an explicit authorized quantity cap.

The production q=1 executor therefore stays q=1 until its caller intentionally
raises ``authorized_max_quantity`` after reviewed live evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

D = Decimal
DEFAULT_TIERS = (1, 5, 10, 25, 50, 100)


@dataclass(frozen=True)
class QuantitySafetyGates:
    counterpart_certified: bool
    rule_fingerprint_certified: bool
    participation_certified: bool
    quote_fresh: bool
    quote_skew_ok: bool
    persistence_ok: bool
    final_revalidation_ok: bool
    capital_snapshot_fresh: bool
    no_unresolved_exposure: bool

    def failures(self) -> list[str]:
        return [name for name, value in self.__dict__.items() if not value]


@dataclass(frozen=True)
class QuantityDecision:
    quantity: int
    status: str
    reason: str
    authorized_max_quantity: int
    available_capacity_tier: int
    modeled_locked_profit: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "status": self.status,
            "reason": self.reason,
            "authorized_max_quantity": self.authorized_max_quantity,
            "available_capacity_tier": self.available_capacity_tier,
            "modeled_locked_profit": str(self.modeled_locked_profit),
            "order_entry": False,
        }


def choose_quantity(
    capacity: dict[str, Any],
    *,
    gates: QuantitySafetyGates,
    authorized_max_quantity: int = 1,
    allowed_tiers: Iterable[int] = DEFAULT_TIERS,
    min_profit_per_contract: Decimal = D("0.03"),
    max_net_cost: Decimal | None = None,
) -> QuantityDecision:
    """Choose the largest *authorized* qualifying tier, failing closed on any gate.

    ``capacity`` should come from ``sports_capacity_math.evaluate_direction`` with
    current venue balances supplied when live sizing is contemplated. A caller may
    pass a higher ``authorized_max_quantity`` only after explicit promotion review.
    """
    authorized = int(authorized_max_quantity)
    if authorized < 1:
        return QuantityDecision(0, "BLOCKED", "AUTHORIZED_MAX_BELOW_ONE", authorized, 0, D("0"))

    failures = gates.failures()
    if failures:
        return QuantityDecision(
            0,
            "BLOCKED",
            "SAFETY_GATE_FAILED:" + ",".join(sorted(failures)),
            authorized,
            int(capacity.get("largest_qualifying_tier") or 0),
            D("0"),
        )

    rows = {int(row.get("quantity") or 0): row for row in capacity.get("tiers") or []}
    permitted = sorted({int(q) for q in allowed_tiers if int(q) > 0 and int(q) <= authorized}, reverse=True)
    available_tier = int(capacity.get("largest_qualifying_tier") or 0)
    for q in permitted:
        row = rows.get(q)
        if not row or str(row.get("status")) != "QUALIFIES":
            continue
        unit_profit = D(str(row.get("profit_per_contract") or "0"))
        locked_profit = D(str(row.get("locked_profit") or "0"))
        net_cost = D(str(row.get("net_cost") or "0"))
        if unit_profit < D(min_profit_per_contract):
            continue
        if max_net_cost is not None and net_cost > D(max_net_cost):
            continue
        return QuantityDecision(
            q,
            "PROPOSED",
            "ALL_GATES_PASS_WITHIN_EXPLICIT_AUTHORIZATION",
            authorized,
            available_tier,
            locked_profit,
        )

    return QuantityDecision(
        0,
        "BLOCKED",
        "NO_AUTHORIZED_QUALIFYING_TIER",
        authorized,
        available_tier,
        D("0"),
    )
