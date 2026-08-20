"""Pure fee-aware Kalshi hedge-cushion math for Gemini/Kalshi 15m research.

This module contains no network or order-entry code.  It answers one question after a
pair has already passed the existing live gates: how far can the Kalshi FOK limit be
made more aggressive while the exact outbound pair still guarantees at least the
configured after-fee profit floor?

Kalshi V2 prices are always in YES space:
* side=bid buys YES, so more aggressive means a HIGHER YES limit;
* side=ask sells YES (economically buys NO), so more aggressive means a LOWER YES limit.

Every candidate cent is recomputed with the existing production fee functions.  This
matters because Kalshi's quadratic fee is rounded up to a cent and can change at a
price step.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit

D = Decimal
CENT = D("0.01")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")


@dataclass(frozen=True)
class HedgeCushion:
    side: str
    original_yes_price: Decimal
    cushioned_yes_price: Decimal
    original_locked_profit: Decimal
    cushioned_locked_profit: Decimal
    profit_floor: Decimal
    cushion_cents: int
    payload: dict[str, Any]

    @property
    def edge_spent(self) -> Decimal:
        return self.original_locked_profit - self.cushioned_locked_profit

    @property
    def has_cushion(self) -> bool:
        return self.cushion_cents > 0


def _candidate_prices(side: str, original: Decimal):
    if side == "bid":
        price = original
        while price <= MAX_PRICE:
            yield price
            price += CENT
    elif side == "ask":
        price = original
        while price >= MIN_PRICE:
            yield price
            price -= CENT
    else:
        raise ValueError(f"unsupported Kalshi side={side!r}")


def best_fee_aware_kalshi_hedge(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
    profit_floor: Decimal = D("0.03"),
) -> HedgeCushion:
    """Return the most aggressive cent-stepped Kalshi limit preserving ``profit_floor``.

    The Gemini limit is deliberately left unchanged.  That makes this conservative for
    Gemini-first execution: any actual Gemini fill improvement creates additional edge
    but is not spent by this function.
    """
    side = str(kalshi_payload.get("side") or "").lower()
    original = D(str(kalshi_payload.get("price")))
    floor = D(profit_floor)
    if side not in {"bid", "ask"}:
        raise ValueError("Kalshi hedge side must be bid or ask")
    if not (MIN_PRICE <= original <= MAX_PRICE):
        raise ValueError("Kalshi hedge price outside prediction range")
    if floor < 0:
        raise ValueError("profit floor cannot be negative")

    original_profit = recompute_payload_locked_profit(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if original_profit < floor:
        raise ValueError(
            f"original hedge is below profit floor original={original_profit} floor={floor}"
        )

    best_price = original
    best_profit = original_profit
    best_payload = deepcopy(kalshi_payload)
    for candidate in _candidate_prices(side, original):
        payload = deepcopy(kalshi_payload)
        payload["price"] = str(candidate)
        profit = recompute_payload_locked_profit(
            gemini_payload,
            payload,
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        )
        if profit >= floor:
            best_price = candidate
            best_profit = profit
            best_payload = payload

    distance = abs(best_price - original)
    cents = int((distance / CENT).to_integral_value())
    return HedgeCushion(
        side=side,
        original_yes_price=original,
        cushioned_yes_price=best_price,
        original_locked_profit=original_profit,
        cushioned_locked_profit=best_profit,
        profit_floor=floor,
        cushion_cents=cents,
        payload=best_payload,
    )


def kalshi_cumulative_depth(
    book: Any,
    *,
    side: str,
    yes_limit: Decimal,
    quantity: Decimal = D("1"),
) -> dict[str, Any]:
    """Measure cumulative executable Kalshi depth through a YES-space limit.

    ``book.yes`` and ``book.no`` are the reconstructed Kalshi resting bid maps used by
    the production shared WebSocket hub.  For a YES buy, NO bids imply YES asks at
    ``1-no_price``.  For a YES sell, resting YES bids are direct counterparties.
    """
    side = str(side).lower()
    limit = D(yes_limit)
    required = D(quantity)
    if side not in {"bid", "ask"}:
        raise ValueError("Kalshi side must be bid or ask")
    if required <= 0:
        raise ValueError("quantity must be positive")

    crossed: list[tuple[Decimal, Decimal]] = []
    if side == "bid":
        # Buy YES: consume implied YES asks from resting NO bids, cheapest first.
        for no_price, qty in sorted(book.no.items(), key=lambda row: row[0], reverse=True):
            yes_price = D("1") - D(no_price)
            if yes_price <= limit and D(qty) > 0:
                crossed.append((yes_price, D(qty)))
    else:
        # Sell YES / buy NO: consume resting YES bids, best price first.
        for yes_price, qty in sorted(book.yes.items(), key=lambda row: row[0], reverse=True):
            if D(yes_price) >= limit and D(qty) > 0:
                crossed.append((D(yes_price), D(qty)))

    cumulative = sum((qty for _price, qty in crossed), D("0"))
    if crossed:
        worst_price = max(p for p, _q in crossed) if side == "bid" else min(p for p, _q in crossed)
        best_price = min(p for p, _q in crossed) if side == "bid" else max(p for p, _q in crossed)
    else:
        best_price = worst_price = None
    return {
        "side": side,
        "yes_limit": str(limit),
        "required_quantity": str(required),
        "cumulative_quantity": str(cumulative),
        "fillable": cumulative >= required,
        "levels_crossed": len(crossed),
        "best_yes_price": str(best_price) if best_price is not None else None,
        "worst_yes_price": str(worst_price) if worst_price is not None else None,
    }
