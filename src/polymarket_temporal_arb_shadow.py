"""Read-only analytics for same-market Polymarket temporal-arbitrage research.

This module intentionally contains no order-entry code. It turns observed fills
or hypothetical shadow fills into inventory-level metrics so we can distinguish
locked two-sided edge from residual directional exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Side = Literal["UP", "DOWN"]


class InventoryState(str, Enum):
    EMPTY = "EMPTY"
    ONE_SIDED = "ONE_SIDED"
    TWO_SIDED_UNLOCKED = "TWO_SIDED_UNLOCKED"
    LOCKED_POSITIVE = "LOCKED_POSITIVE"


@dataclass(frozen=True)
class Fill:
    side: Side
    price: float
    quantity: float
    fee_usd: float = 0.0
    timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        if self.side not in ("UP", "DOWN"):
            raise ValueError("side must be UP or DOWN")
        if not (0.0 <= self.price <= 1.0):
            raise ValueError("price must be between 0 and 1")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.fee_usd < 0:
            raise ValueError("fee_usd cannot be negative")

    @property
    def all_in_cost(self) -> float:
        return self.price * self.quantity + self.fee_usd


@dataclass
class MarketInventory:
    market_id: str
    min_locked_edge_per_pair: float = 0.03
    fills: list[Fill] = field(default_factory=list)

    def add_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def _side_totals(self, side: Side) -> tuple[float, float]:
        fills = [fill for fill in self.fills if fill.side == side]
        quantity = sum(fill.quantity for fill in fills)
        all_in_cost = sum(fill.all_in_cost for fill in fills)
        return quantity, all_in_cost

    def snapshot(self) -> dict[str, float | str | None]:
        up_qty, up_cost = self._side_totals("UP")
        down_qty, down_cost = self._side_totals("DOWN")

        avg_up = up_cost / up_qty if up_qty else None
        avg_down = down_cost / down_qty if down_qty else None
        matched_qty = min(up_qty, down_qty)

        pair_cost = (
            avg_up + avg_down
            if avg_up is not None and avg_down is not None
            else None
        )
        locked_profit_per_pair = 1.0 - pair_cost if pair_cost is not None else None
        locked_profit_total = (
            matched_qty * locked_profit_per_pair
            if locked_profit_per_pair is not None
            else 0.0
        )

        if up_qty == 0 and down_qty == 0:
            state = InventoryState.EMPTY
        elif up_qty == 0 or down_qty == 0:
            state = InventoryState.ONE_SIDED
        elif (
            locked_profit_per_pair is not None
            and locked_profit_per_pair >= self.min_locked_edge_per_pair
        ):
            state = InventoryState.LOCKED_POSITIVE
        else:
            state = InventoryState.TWO_SIDED_UNLOCKED

        residual_side: str | None
        residual_qty: float
        if up_qty > down_qty:
            residual_side = "UP"
            residual_qty = up_qty - down_qty
        elif down_qty > up_qty:
            residual_side = "DOWN"
            residual_qty = down_qty - up_qty
        else:
            residual_side = None
            residual_qty = 0.0

        return {
            "market_id": self.market_id,
            "state": state.value,
            "up_qty": up_qty,
            "down_qty": down_qty,
            "avg_up_all_in": avg_up,
            "avg_down_all_in": avg_down,
            "matched_qty": matched_qty,
            "pair_cost_all_in": pair_cost,
            "locked_profit_per_pair": locked_profit_per_pair,
            "locked_profit_total": locked_profit_total,
            "residual_side": residual_side,
            "residual_qty": residual_qty,
            "total_cost": up_cost + down_cost,
        }

    def mark_to_fair(self, fair_up_probability: float) -> float:
        """Return shadow inventory EV versus a supplied fair UP probability."""
        if not (0.0 <= fair_up_probability <= 1.0):
            raise ValueError("fair_up_probability must be between 0 and 1")

        up_qty, up_cost = self._side_totals("UP")
        down_qty, down_cost = self._side_totals("DOWN")
        fair_value = (
            up_qty * fair_up_probability
            + down_qty * (1.0 - fair_up_probability)
        )
        return fair_value - up_cost - down_cost
