"""Fee-aware Kalshi hedge cushion for bounded q1-q5 scale canaries.

The cushion spends only modeled excess edge and never permits total after-fee profit
below quantity * minimum per-contract profit.  No network or order-entry code exists
here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_scaled_probe_guard import (
    DEFAULT_MIN_PROFIT_PER_CONTRACT,
    recompute_scaled_payload_locked_profit,
)

D = Decimal
CENT = D("0.01")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")


@dataclass(frozen=True)
class ScaledHedgeCushion:
    side: str
    quantity: Decimal
    original_yes_price: Decimal
    cushioned_yes_price: Decimal
    original_locked_profit: Decimal
    cushioned_locked_profit: Decimal
    total_profit_floor: Decimal
    cushion_cents: int
    payload: dict[str, Any]

    @property
    def edge_spent(self) -> Decimal:
        return self.original_locked_profit - self.cushioned_locked_profit

    @property
    def has_cushion(self) -> bool:
        return self.cushion_cents > 0


def _candidate_prices(side: str, original: D):
    if side == "bid":
        p = original
        while p <= MAX_PRICE:
            yield p
            p += CENT
    elif side == "ask":
        p = original
        while p >= MIN_PRICE:
            yield p
            p -= CENT
    else:
        raise ValueError(f"unsupported Kalshi side={side!r}")


def best_scaled_fee_aware_kalshi_hedge(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
    min_profit_per_contract: Decimal = DEFAULT_MIN_PROFIT_PER_CONTRACT,
    max_cushion_cents: int = 2,
) -> ScaledHedgeCushion:
    side = str(kalshi_payload.get("side") or "").lower()
    original = D(str(kalshi_payload.get("price")))
    q = D(str(gemini_payload.get("quantity")))
    if q != D(str(kalshi_payload.get("count"))):
        raise ValueError("venue quantities differ before scaled cushion")
    if side not in {"bid", "ask"}:
        raise ValueError("Kalshi hedge side must be bid or ask")
    if not MIN_PRICE <= original <= MAX_PRICE:
        raise ValueError("Kalshi hedge price outside prediction range")
    if max_cushion_cents < 0:
        raise ValueError("max_cushion_cents must be nonnegative")

    total_floor = q * D(min_profit_per_contract)
    original_profit = recompute_scaled_payload_locked_profit(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if original_profit < total_floor:
        raise ValueError(
            f"original scaled hedge below total floor original={original_profit} floor={total_floor}"
        )

    best_price = original
    best_profit = original_profit
    best_payload = deepcopy(kalshi_payload)
    for candidate in _candidate_prices(side, original):
        cents = int((abs(candidate - original) / CENT).to_integral_value())
        if cents > max_cushion_cents:
            break
        payload = deepcopy(kalshi_payload)
        payload["price"] = str(candidate)
        profit = recompute_scaled_payload_locked_profit(
            gemini_payload,
            payload,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        )
        if profit >= total_floor:
            best_price = candidate
            best_profit = profit
            best_payload = payload

    cents = int((abs(best_price - original) / CENT).to_integral_value())
    return ScaledHedgeCushion(
        side=side,
        quantity=q,
        original_yes_price=original,
        cushioned_yes_price=best_price,
        original_locked_profit=original_profit,
        cushioned_locked_profit=best_profit,
        total_profit_floor=total_floor,
        cushion_cents=cents,
        payload=best_payload,
    )
