"""Pure policy helpers for q=1 dynamic complement completion.

This module contains no venue mutation.  It decides how to finish a complete set
after the first maker fill while preserving a hard fee-adjusted edge.

Policy:
- preserve edge / completion budget at entry;
- after one side fills, first take the complement immediately if the live taker
  price still preserves the requested edge after the higher taker fee;
- otherwise move the missing maker quote to the most aggressive non-crossing price
  that preserves the edge;
- projected rebates/rewards never enter the edge calculation.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any

from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)

ONE = D("1")
CENT = D("0.01")
ZERO = D("0")
DEFAULT_FINAL_EDGE = D("0.03")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")


def _d(value: Any) -> D:
    return D(str(value))


def floor_to_increment(value: D, increment: D = CENT) -> D:
    value, increment = _d(value), _d(increment)
    if increment <= ZERO:
        raise ValueError("price increment must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def completed_pair_edge(*, filled_price: D, complement_price: D, complement_is_taker: bool = False) -> D:
    """Fee-adjusted q=1 complete-set edge, excluding all rewards/rebates."""
    filled_price, complement_price = _d(filled_price), _d(complement_price)
    if not (ZERO < filled_price < ONE and ZERO < complement_price < ONE):
        raise ValueError("prices must be inside (0,1)")
    filled_fee = gemini_predictions_maker_fee(filled_price, ONE)
    fee_fn = gemini_predictions_taker_fee if complement_is_taker else gemini_predictions_maker_fee
    complement_fee = fee_fn(complement_price, ONE)
    return ONE - filled_price - complement_price - filled_fee - complement_fee


def max_edge_safe_complement_price(
    *, filled_price: D, min_final_edge: D = DEFAULT_FINAL_EDGE,
    increment: D = CENT, min_price: D = MIN_PRICE, max_price: D = MAX_PRICE,
) -> D | None:
    filled_price, min_final_edge, increment = _d(filled_price), _d(min_final_edge), _d(increment)
    min_price, max_price = _d(min_price), _d(max_price)
    if min_final_edge < ZERO:
        raise ValueError("final edge floor may not be negative")
    candidate = floor_to_increment(max_price, increment)
    while candidate >= min_price:
        if completed_pair_edge(filled_price=filled_price, complement_price=candidate) >= min_final_edge:
            return candidate
        candidate -= increment
    return None


def maker_passive_ceiling(*, missing_outcome: str, yes_bid: D, yes_ask: D, increment: D = CENT) -> D:
    missing = str(missing_outcome).strip().upper()
    yes_bid, yes_ask, increment = _d(yes_bid), _d(yes_ask), _d(increment)
    if not (ZERO < yes_bid < yes_ask < ONE):
        raise ValueError("invalid YES book")
    if missing == "YES":
        ceiling = yes_ask - increment
    elif missing == "NO":
        ceiling = (ONE - yes_bid) - increment
    else:
        raise ValueError("missing_outcome must be YES or NO")
    return floor_to_increment(ceiling, increment)


def complement_taker_price(*, missing_outcome: str, yes_bid: D, yes_ask: D) -> D:
    missing = str(missing_outcome).strip().upper()
    yes_bid, yes_ask = _d(yes_bid), _d(yes_ask)
    if not (ZERO < yes_bid < yes_ask < ONE):
        raise ValueError("invalid YES book")
    if missing == "YES":
        return yes_ask
    if missing == "NO":
        return ONE - yes_bid
    raise ValueError("missing_outcome must be YES or NO")


def taker_rescue_plan(
    *, filled_outcome: str, filled_price: D, yes_bid: D, yes_ask: D,
    min_final_edge: D = DEFAULT_FINAL_EDGE,
) -> dict[str, Any]:
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    missing = "NO" if filled == "YES" else "YES"
    price = complement_taker_price(missing_outcome=missing, yes_bid=yes_bid, yes_ask=yes_ask)
    edge = completed_pair_edge(
        filled_price=_d(filled_price), complement_price=price, complement_is_taker=True
    )
    return {
        "action": "TAKE_COMPLEMENT_NOW" if edge >= _d(min_final_edge) else "DO_NOT_TAKE",
        "filled_outcome": filled,
        "missing_outcome": missing,
        "taker_price": price,
        "final_edge": edge,
        "min_final_edge": _d(min_final_edge),
    }


def dynamic_complement_plan(
    *, filled_outcome: str, filled_price: D, current_complement_price: D,
    yes_bid: D, yes_ask: D, min_final_edge: D = DEFAULT_FINAL_EDGE,
    increment: D = CENT, min_price: D = MIN_PRICE, max_price: D = MAX_PRICE,
) -> dict[str, Any]:
    """Choose immediate taker completion when profitable; otherwise best safe maker reprice."""
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    missing = "NO" if filled == "YES" else "YES"
    filled_price, current = _d(filled_price), _d(current_complement_price)

    taker = taker_rescue_plan(
        filled_outcome=filled, filled_price=filled_price,
        yes_bid=yes_bid, yes_ask=yes_ask, min_final_edge=min_final_edge,
    )
    if taker["action"] == "TAKE_COMPLEMENT_NOW":
        return taker

    edge_ceiling = max_edge_safe_complement_price(
        filled_price=filled_price, min_final_edge=min_final_edge,
        increment=increment, min_price=min_price, max_price=max_price,
    )
    passive_ceiling = maker_passive_ceiling(
        missing_outcome=missing, yes_bid=yes_bid, yes_ask=yes_ask, increment=increment,
    )
    if edge_ceiling is None:
        return {
            "action": "HOLD_OR_CANCEL", "filled_outcome": filled,
            "missing_outcome": missing, "reason": "NO_PRICE_PRESERVES_FINAL_EDGE",
            "target_price": None, "edge_ceiling": None, "passive_ceiling": passive_ceiling,
            "taker_final_edge": taker["final_edge"],
        }
    target = floor_to_increment(min(edge_ceiling, passive_ceiling, _d(max_price)), _d(increment))
    if target < _d(min_price) or target <= current:
        return {
            "action": "HOLD_OR_CANCEL", "filled_outcome": filled,
            "missing_outcome": missing, "reason": "NO_SAFE_PRICE_IMPROVEMENT",
            "target_price": current, "edge_ceiling": edge_ceiling,
            "passive_ceiling": passive_ceiling,
            "final_edge": completed_pair_edge(filled_price=filled_price, complement_price=current),
            "taker_final_edge": taker["final_edge"],
        }
    final_edge = completed_pair_edge(filled_price=filled_price, complement_price=target)
    if final_edge < _d(min_final_edge):
        raise AssertionError("dynamic complement target violated edge floor")
    return {
        "action": "REPRICE_MAKER_COMPLEMENT", "filled_outcome": filled,
        "missing_outcome": missing, "target_price": target, "prior_price": current,
        "price_improvement": target - current, "edge_ceiling": edge_ceiling,
        "passive_ceiling": passive_ceiling, "final_edge": final_edge,
        "taker_final_edge": taker["final_edge"], "min_final_edge": _d(min_final_edge),
    }
