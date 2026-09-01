"""Pure inventory-aware policy helpers for the l5-style Gemini strategy.

This module contains no network or order-entry code. It is intentionally separate
from the currently running shadow so we can evaluate improved policy logic without
changing the pinned experiment.

Core idea: once one outcome fills, stop treating the next quote as a fresh
threshold bet. Instead compute the most we can pay for the complementary outcome
while preserving a configured locked pair edge after maker fees.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
DEFAULT_PAIR_FLOOR = D("0.01")
DEFAULT_TICK = D("0.01")


@dataclass(frozen=True)
class CompletionBudget:
    held_side: str
    held_price: D
    held_fee: D
    completion_side: str
    max_completion_price: D | None
    locked_edge_at_cap: D | None


@dataclass(frozen=True)
class InventoryDecision:
    mode: str
    target_side: str | None
    quote: D | None
    max_price: D | None
    locked_edge_if_fill: D | None
    allow_new_directional_inventory: bool
    reason: str


def locked_pair_edge(
    held_price: D,
    held_fee: D,
    completion_price: D,
    *,
    quantity: D = D("1"),
) -> D:
    """Locked payout edge for one complementary pair, before any maker rebate.

    ``held_fee`` is the fee already incurred on the filled residual unit. The
    prospective completion leg uses the current Gemini maker-fee schedule.
    Rebates are deliberately excluded from the lock calculation.
    """
    held_price = D(str(held_price))
    held_fee = D(str(held_fee))
    completion_price = D(str(completion_price))
    quantity = D(str(quantity))
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if not (D("0") <= held_price <= ONE and D("0") <= completion_price <= ONE):
        raise ValueError("prices must be in [0, 1]")
    completion_fee = gemini_predictions_maker_fee(completion_price, quantity)
    return quantity - (held_price * quantity) - held_fee - (completion_price * quantity) - completion_fee


def completion_budget(
    *,
    held_side: str,
    held_price: D,
    held_fee: D,
    pair_floor: D = DEFAULT_PAIR_FLOOR,
    price_increment: D = DEFAULT_TICK,
) -> CompletionBudget:
    """Return the highest complementary maker price that preserves ``pair_floor``.

    Search is performed on the actual price grid because Gemini maker fees round
    up to cents, making a simple closed-form threshold misleading around fee
    boundaries.
    """
    side = held_side.upper()
    if side not in {"YES", "NO"}:
        raise ValueError("held_side must be YES or NO")
    held_price = D(str(held_price))
    held_fee = D(str(held_fee))
    pair_floor = D(str(pair_floor))
    tick = D(str(price_increment))
    if tick <= 0:
        raise ValueError("price_increment must be positive")
    if pair_floor < 0:
        raise ValueError("pair_floor must be non-negative")

    best: D | None = None
    best_edge: D | None = None
    price = tick
    while price < ONE:
        edge = locked_pair_edge(held_price, held_fee, price)
        if edge >= pair_floor:
            best = price
            best_edge = edge
        price += tick

    return CompletionBudget(
        held_side=side,
        held_price=held_price,
        held_fee=held_fee,
        completion_side="NO" if side == "YES" else "YES",
        max_completion_price=best,
        locked_edge_at_cap=best_edge,
    )


def inventory_decision(
    *,
    yes_qty: D,
    no_qty: D,
    residual_unit_price: D | None,
    residual_unit_fee: D | None,
    complementary_resting_quote: D | None,
    complementary_passive_ceiling: D | None,
    seconds_to_expiry: float,
    new_inventory_cutoff_s: float,
    pair_floor: D = DEFAULT_PAIR_FLOOR,
    price_increment: D = DEFAULT_TICK,
) -> InventoryDecision:
    """Choose between fresh accumulation, profitable pair completion, or waiting.

    This is deliberately conservative:
    * when flat/balanced, new directional inventory is allowed only before the
      configured cutoff;
    * when residual inventory exists, only the complementary side is targeted;
    * completion can improve toward the market only up to the price that still
      preserves ``pair_floor`` after maker fees;
    * no rebate is needed to justify the pair.

    ``residual_unit_price`` and ``residual_unit_fee`` should describe the actual
    unmatched unit. For q=1 this is exact. For larger inventory, the caller should
    supply FIFO/LIFO residual cost rather than a blended VWAP.
    """
    yes_qty = D(str(yes_qty))
    no_qty = D(str(no_qty))
    if yes_qty < 0 or no_qty < 0:
        raise ValueError("inventory cannot be negative")

    if yes_qty == no_qty:
        if seconds_to_expiry <= new_inventory_cutoff_s:
            return InventoryDecision(
                mode="FREEZE_LATE_FLAT",
                target_side=None,
                quote=None,
                max_price=None,
                locked_edge_if_fill=None,
                allow_new_directional_inventory=False,
                reason="Do not open fresh residual inventory near expiry.",
            )
        return InventoryDecision(
            mode="ACCUMULATE",
            target_side=None,
            quote=None,
            max_price=None,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=True,
            reason="Inventory is balanced; threshold/fair-value signal may open a new unit.",
        )

    held_side = "YES" if yes_qty > no_qty else "NO"
    completion_side = "NO" if held_side == "YES" else "YES"
    if residual_unit_price is None or residual_unit_fee is None:
        return InventoryDecision(
            mode="HOLD_RESIDUAL_NO_COST_BASIS",
            target_side=completion_side,
            quote=None,
            max_price=None,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=False,
            reason="Residual inventory exists but exact unmatched cost basis is unavailable.",
        )

    budget = completion_budget(
        held_side=held_side,
        held_price=D(str(residual_unit_price)),
        held_fee=D(str(residual_unit_fee)),
        pair_floor=pair_floor,
        price_increment=price_increment,
    )
    cap = budget.max_completion_price
    if cap is None:
        return InventoryDecision(
            mode="WAIT_FOR_COMPLEMENT",
            target_side=completion_side,
            quote=None,
            max_price=None,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=False,
            reason="No complementary maker price on the grid can preserve the pair floor.",
        )

    if complementary_resting_quote is None or complementary_passive_ceiling is None:
        return InventoryDecision(
            mode="WAIT_FOR_COMPLEMENT",
            target_side=completion_side,
            quote=None,
            max_price=cap,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=False,
            reason="Complementary book is unavailable.",
        )

    resting = D(str(complementary_resting_quote))
    passive_ceiling = D(str(complementary_passive_ceiling))
    if resting > cap:
        return InventoryDecision(
            mode="WAIT_FOR_COMPLEMENT",
            target_side=completion_side,
            quote=None,
            max_price=cap,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=False,
            reason="Current complementary maker quote is too expensive to lock the configured edge.",
        )

    # Pair completion is risk reduction, so improve toward the market as far as
    # passive execution and the profit cap allow. This is intentionally more
    # aggressive than the one-tick directional skew used to open inventory.
    quote = min(cap, passive_ceiling)
    if quote < resting:
        quote = resting
    edge = locked_pair_edge(D(str(residual_unit_price)), D(str(residual_unit_fee)), quote)
    if edge < D(str(pair_floor)):
        # Defensive guard against unexpected grid/fee behavior.
        return InventoryDecision(
            mode="WAIT_FOR_COMPLEMENT",
            target_side=completion_side,
            quote=None,
            max_price=cap,
            locked_edge_if_fill=None,
            allow_new_directional_inventory=False,
            reason="Candidate completion quote failed the after-fee pair-floor recheck.",
        )

    return InventoryDecision(
        mode="PAIR_COMPLETE",
        target_side=completion_side,
        quote=quote,
        max_price=cap,
        locked_edge_if_fill=edge,
        allow_new_directional_inventory=False,
        reason="Residual inventory exists; prioritize complementary fill while preserving locked edge.",
    )
