"""Pure economics for bounded Gemini L5 q=1 complement completion.

No venue mutation lives here.  The live pair primitive uses these helpers to:
- require enough initial fee-adjusted edge to fund at least one cent of
  complement improvement regardless of which side fills first;
- compute the highest complement price that still preserves the configured
  final complete-pair edge; and
- cap that price at the maker-passive (MOC/post-only) ceiling from a fresh YES
  book.

Rewards/rebates are deliberately excluded from every edge calculation.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")


def _d(value: Any) -> D:
    return D(str(value))


def floor_to_tick(value: D, tick: D = TICK) -> D:
    value, tick = _d(value), _d(tick)
    if tick <= ZERO:
        raise ValueError("tick must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def completed_edge(*, filled_price: D, complement_price: D) -> D:
    """Fee-adjusted q=1 complete-pair edge, excluding rewards/rebates."""
    filled_price, complement_price = _d(filled_price), _d(complement_price)
    if not (ZERO < filled_price < ONE and ZERO < complement_price < ONE):
        raise ValueError("prices must be inside (0,1)")
    return (
        ONE
        - filled_price
        - complement_price
        - gemini_predictions_maker_fee(filled_price, ONE)
        - gemini_predictions_maker_fee(complement_price, ONE)
    )


def max_edge_safe_complement_price(
    *,
    filled_price: D,
    min_final_edge: D,
    tick: D = TICK,
    min_price: D = MIN_PRICE,
    max_price: D = MAX_PRICE,
) -> D | None:
    """Highest tick price that still preserves the requested final edge."""
    filled_price = _d(filled_price)
    min_final_edge = _d(min_final_edge)
    tick = _d(tick)
    min_price, max_price = _d(min_price), _d(max_price)
    if min_final_edge < ZERO:
        raise ValueError("final edge floor may not be negative")
    candidate = floor_to_tick(max_price, tick)
    while candidate >= min_price:
        if completed_edge(filled_price=filled_price, complement_price=candidate) >= min_final_edge:
            return candidate
        candidate -= tick
    return None


def maker_passive_ceiling(
    *,
    missing_outcome: str,
    yes_bid: D,
    yes_ask: D,
    tick: D = TICK,
) -> D:
    """Highest non-crossing maker price for the missing outcome."""
    missing = str(missing_outcome).strip().upper()
    yes_bid, yes_ask, tick = _d(yes_bid), _d(yes_ask), _d(tick)
    if not (ZERO < yes_bid < yes_ask < ONE):
        raise ValueError("invalid YES book")
    if missing == "YES":
        ceiling = yes_ask - tick
    elif missing == "NO":
        # NO taker price is implied by the YES bid.
        ceiling = (ONE - yes_bid) - tick
    else:
        raise ValueError("missing_outcome must be YES or NO")
    return floor_to_tick(ceiling, tick)


def reserve_ok_for_either_first_fill(
    *,
    yes_price: D,
    no_price: D,
    min_final_edge: D,
    reserve_ticks: int = 1,
    tick: D = TICK,
    max_price: D = MAX_PRICE,
) -> bool:
    """Require economic room to improve either possible complement by N ticks.

    This is intentionally independent of the future book.  It proves only that
    the trade economics can afford the configured rescue move if maker passivity
    later permits it.
    """
    yes_price, no_price = _d(yes_price), _d(no_price)
    min_final_edge, tick, max_price = _d(min_final_edge), _d(tick), _d(max_price)
    n = int(reserve_ticks)
    if n < 1:
        raise ValueError("reserve_ticks must be >=1")
    spend = tick * n
    if yes_price + spend > max_price or no_price + spend > max_price:
        return False
    return (
        completed_edge(filled_price=yes_price, complement_price=no_price + spend) >= min_final_edge
        and completed_edge(filled_price=no_price, complement_price=yes_price + spend) >= min_final_edge
    )


def complement_plan(
    *,
    filled_outcome: str,
    filled_price: D,
    current_complement_price: D,
    yes_bid: D,
    yes_ask: D,
    min_final_edge: D,
    tick: D = TICK,
    min_price: D = MIN_PRICE,
    max_price: D = MAX_PRICE,
) -> dict[str, Any]:
    """Most aggressive maker-only complement target that preserves final edge."""
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    missing = "NO" if filled == "YES" else "YES"
    filled_price = _d(filled_price)
    current = _d(current_complement_price)
    edge_ceiling = max_edge_safe_complement_price(
        filled_price=filled_price,
        min_final_edge=min_final_edge,
        tick=tick,
        min_price=min_price,
        max_price=max_price,
    )
    passive_ceiling = maker_passive_ceiling(
        missing_outcome=missing,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tick=tick,
    )
    if edge_ceiling is None:
        return {
            "action": "HOLD",
            "reason": "NO_EDGE_SAFE_COMPLEMENT_PRICE",
            "missing_outcome": missing,
            "target_price": None,
            "edge_ceiling": None,
            "passive_ceiling": passive_ceiling,
        }
    target = floor_to_tick(min(edge_ceiling, passive_ceiling, _d(max_price)), _d(tick))
    if target <= current or target < _d(min_price):
        return {
            "action": "HOLD",
            "reason": "NO_SAFE_PRICE_IMPROVEMENT",
            "missing_outcome": missing,
            "target_price": current,
            "edge_ceiling": edge_ceiling,
            "passive_ceiling": passive_ceiling,
            "final_edge": completed_edge(filled_price=filled_price, complement_price=current),
        }
    final_edge = completed_edge(filled_price=filled_price, complement_price=target)
    if final_edge < _d(min_final_edge):
        raise AssertionError("completion target violated final edge floor")
    return {
        "action": "REPRICE",
        "reason": "SPEND_RESERVED_EDGE",
        "missing_outcome": missing,
        "target_price": target,
        "prior_price": current,
        "price_improvement": target - current,
        "edge_ceiling": edge_ceiling,
        "passive_ceiling": passive_ceiling,
        "final_edge": final_edge,
    }
