"""Fee-aware Kalshi hedge cushion for future q=10 execution research.

This is deliberately capped at two cents and requires the exact q=10 pair to retain
at least 3c after fees per contract at every candidate price. It contains no network
or order-entry code.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_q10_probe_guard import (
    MIN_PRICE,
    MAX_PRICE,
    MIN_PROFIT_PER_CONTRACT,
    Q10,
    q10_economics,
)

D = Decimal
CENT = D("0.01")


@dataclass(frozen=True)
class Q10HedgeCushion:
    side: str
    original_yes_price: Decimal
    cushioned_yes_price: Decimal
    original_locked_profit: Decimal
    cushioned_locked_profit: Decimal
    total_profit_floor: Decimal
    cushion_cents: int
    payload: dict[str, Any]


def best_q10_fee_aware_kalshi_hedge(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
    min_profit_per_contract: Decimal = MIN_PROFIT_PER_CONTRACT,
    max_cushion_cents: int = 2,
) -> Q10HedgeCushion:
    side = str(kalshi_payload.get("side") or "").lower()
    if side not in {"bid", "ask"}:
        raise ValueError("q10 Kalshi hedge side must be bid or ask")
    if max_cushion_cents < 0 or max_cushion_cents > 2:
        raise ValueError("q10 hedge cushion must be capped at 0..2 cents")

    original = D(str(kalshi_payload.get("price")))
    if not (MIN_PRICE <= original <= MAX_PRICE):
        raise ValueError("q10 original Kalshi YES price outside prediction range")
    floor_per_contract = D(min_profit_per_contract)
    total_floor = Q10 * floor_per_contract
    original_e = q10_economics(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if original_e.locked_profit < total_floor:
        raise ValueError("q10 original hedge economics below profit floor")

    best_price = original
    best_e = original_e
    best_cents = 0
    best_payload = deepcopy(kalshi_payload)
    for cents in range(1, int(max_cushion_cents) + 1):
        candidate = original + CENT * cents if side == "bid" else original - CENT * cents
        if not (MIN_PRICE <= candidate <= MAX_PRICE):
            break
        payload = deepcopy(kalshi_payload)
        payload["price"] = str(candidate)
        e = q10_economics(
            gemini_payload,
            payload,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        )
        if e.locked_profit >= total_floor:
            best_price = candidate
            best_e = e
            best_cents = cents
            best_payload = payload
        else:
            break

    return Q10HedgeCushion(
        side=side,
        original_yes_price=original,
        cushioned_yes_price=best_price,
        original_locked_profit=original_e.locked_profit,
        cushioned_locked_profit=best_e.locked_profit,
        total_profit_floor=total_floor,
        cushion_cents=best_cents,
        payload=best_payload,
    )
