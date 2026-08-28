"""Pure policy helpers for q=1 dynamic complement completion.

This module contains no venue mutation. It answers two related questions:
1) before any fill, how much excess pair edge can be spent on better maker queue
   position while preserving a hard fee-adjusted entry edge; and
2) after the first maker fill, how far can the missing outcome be repriced while
   remaining maker-only and preserving a hard fee-adjusted complete-set edge?

The key policy distinction is deliberate:
- raw entry quotes qualify the opportunity;
- excess raw edge may be spent symmetrically to improve initial maker queue position;
- after one side fills, the missing side may spend the remaining completion budget;
- projected rebates/rewards never enter any edge calculation.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
CENT = D("0.01")
ZERO = D("0")
DEFAULT_FINAL_EDGE = D("0.03")
DEFAULT_INITIAL_TARGET_EDGE = D("0.10")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")


def _d(value: Any) -> D:
    return D(str(value))


def floor_to_increment(value: D, increment: D = CENT) -> D:
    value, increment = _d(value), _d(increment)
    if increment <= ZERO:
        raise ValueError("price increment must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def completed_pair_edge(*, filled_price: D, complement_price: D) -> D:
    """Fee-adjusted q=1 complete-set edge, excluding all rewards/rebates."""
    filled_price, complement_price = _d(filled_price), _d(complement_price)
    if not (ZERO < filled_price < ONE and ZERO < complement_price < ONE):
        raise ValueError("prices must be inside (0,1)")
    filled_fee = gemini_predictions_maker_fee(filled_price, ONE)
    complement_fee = gemini_predictions_maker_fee(complement_price, ONE)
    return ONE - filled_price - complement_price - filled_fee - complement_fee


def max_edge_safe_complement_price(
    *,
    filled_price: D,
    min_final_edge: D = DEFAULT_FINAL_EDGE,
    increment: D = CENT,
    min_price: D = MIN_PRICE,
    max_price: D = MAX_PRICE,
) -> D | None:
    """Highest cent price that preserves the requested complete-set edge."""
    filled_price = _d(filled_price)
    min_final_edge = _d(min_final_edge)
    increment = _d(increment)
    min_price, max_price = _d(min_price), _d(max_price)
    if min_final_edge < ZERO:
        raise ValueError("final edge floor may not be negative")
    candidate = floor_to_increment(max_price, increment)
    while candidate >= min_price:
        if completed_pair_edge(filled_price=filled_price, complement_price=candidate) >= min_final_edge:
            return candidate
        candidate -= increment
    return None


def maker_passive_ceiling(
    *,
    missing_outcome: str,
    yes_bid: D,
    yes_ask: D,
    increment: D = CENT,
) -> D:
    """Highest non-crossing maker price for one outcome.

    Gemini exposes the YES contract book. For NO, the opposing ask implied by the
    YES bid is 1-yes_bid. We stay one tick below the relevant taker price so the
    order remains MOC/post-only rather than intentionally crossing.
    """
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


def aggressive_entry_plan(
    *,
    yes_bid: D,
    yes_ask: D,
    min_entry_edge: D = DEFAULT_INITIAL_TARGET_EDGE,
    increment: D = CENT,
    min_price: D = MIN_PRICE,
    max_price: D = MAX_PRICE,
) -> dict[str, Any]:
    """Spend excess raw pair edge on balanced initial maker queue improvement.

    The raw maker pair is YES at the YES bid and NO at 1-YES ask. We only improve
    those bids if the resulting complete-set economics still preserve
    ``min_entry_edge`` after maker fees. Every proposed quote remains at or below
    its maker-passive ceiling, inside the configured price band, and we improve the
    side with less accumulated price improvement first so one outcome is not made
    materially more aggressive merely because it is cheaper.

    This is a pure policy helper. It does not submit, cancel, or mutate orders.
    """
    yes_bid, yes_ask = _d(yes_bid), _d(yes_ask)
    min_entry_edge, increment = _d(min_entry_edge), _d(increment)
    min_price, max_price = _d(min_price), _d(max_price)
    if not (ZERO < yes_bid < yes_ask < ONE):
        raise ValueError("invalid YES book")
    if min_entry_edge < ZERO:
        raise ValueError("entry edge floor may not be negative")
    if increment <= ZERO:
        raise ValueError("price increment must be positive")

    raw_yes = yes_bid
    raw_no = ONE - yes_ask
    raw_edge = completed_pair_edge(filled_price=raw_yes, complement_price=raw_no)
    yes_ceiling = min(
        maker_passive_ceiling(missing_outcome="YES", yes_bid=yes_bid, yes_ask=yes_ask, increment=increment),
        max_price,
    )
    no_ceiling = min(
        maker_passive_ceiling(missing_outcome="NO", yes_bid=yes_bid, yes_ask=yes_ask, increment=increment),
        max_price,
    )

    base = {
        "raw_yes_quote": raw_yes,
        "raw_no_quote": raw_no,
        "raw_edge": raw_edge,
        "yes_passive_ceiling": yes_ceiling,
        "no_passive_ceiling": no_ceiling,
        "min_entry_edge": min_entry_edge,
    }
    if not (min_price <= raw_yes <= max_price and min_price <= raw_no <= max_price):
        return {
            **base,
            "action": "HOLD_RAW",
            "reason": "RAW_QUOTE_OUTSIDE_PRICE_BAND",
            "yes_quote": raw_yes,
            "no_quote": raw_no,
            "final_edge": raw_edge,
            "yes_improvement": ZERO,
            "no_improvement": ZERO,
        }
    if raw_edge < min_entry_edge:
        return {
            **base,
            "action": "HOLD_RAW",
            "reason": "INSUFFICIENT_EXCESS_EDGE",
            "yes_quote": raw_yes,
            "no_quote": raw_no,
            "final_edge": raw_edge,
            "yes_improvement": ZERO,
            "no_improvement": ZERO,
        }

    yes_quote, no_quote = raw_yes, raw_no
    while True:
        candidates: list[tuple[str, D, D]] = []
        if yes_quote + increment <= yes_ceiling:
            next_yes = yes_quote + increment
            if completed_pair_edge(filled_price=next_yes, complement_price=no_quote) >= min_entry_edge:
                candidates.append(("YES", yes_quote - raw_yes, yes_quote))
        if no_quote + increment <= no_ceiling:
            next_no = no_quote + increment
            if completed_pair_edge(filled_price=yes_quote, complement_price=next_no) >= min_entry_edge:
                candidates.append(("NO", no_quote - raw_no, no_quote))
        if not candidates:
            break
        side = min(candidates, key=lambda item: (item[1], item[2], 0 if item[0] == "YES" else 1))[0]
        if side == "YES":
            yes_quote += increment
        else:
            no_quote += increment

    final_edge = completed_pair_edge(filled_price=yes_quote, complement_price=no_quote)
    if final_edge < min_entry_edge:
        raise AssertionError("aggressive entry target violated edge floor")
    action = "IMPROVE_INITIAL_MAKERS" if (yes_quote > raw_yes or no_quote > raw_no) else "HOLD_RAW"
    return {
        **base,
        "action": action,
        "reason": "EXCESS_EDGE_SPENT_BALANCED" if action == "IMPROVE_INITIAL_MAKERS" else "NO_SAFE_PRICE_IMPROVEMENT",
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "final_edge": final_edge,
        "yes_improvement": yes_quote - raw_yes,
        "no_improvement": no_quote - raw_no,
    }


def dynamic_complement_plan(
    *,
    filled_outcome: str,
    filled_price: D,
    current_complement_price: D,
    yes_bid: D,
    yes_ask: D,
    min_final_edge: D = DEFAULT_FINAL_EDGE,
    increment: D = CENT,
    min_price: D = MIN_PRICE,
    max_price: D = MAX_PRICE,
) -> dict[str, Any]:
    """Return the safest most-aggressive maker reprice after the first fill.

    The target is min(edge ceiling, passive/MOC ceiling, incentive-band ceiling).
    We never move the complement backwards; if there is no safe improvement the
    action is HOLD_OR_CANCEL rather than accepting negative trade economics.
    """
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    missing = "NO" if filled == "YES" else "YES"
    filled_price = _d(filled_price)
    current = _d(current_complement_price)
    edge_ceiling = max_edge_safe_complement_price(
        filled_price=filled_price,
        min_final_edge=min_final_edge,
        increment=increment,
        min_price=min_price,
        max_price=max_price,
    )
    passive_ceiling = maker_passive_ceiling(
        missing_outcome=missing,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        increment=increment,
    )
    if edge_ceiling is None:
        return {
            "action": "HOLD_OR_CANCEL",
            "filled_outcome": filled,
            "missing_outcome": missing,
            "reason": "NO_PRICE_PRESERVES_FINAL_EDGE",
            "target_price": None,
            "edge_ceiling": None,
            "passive_ceiling": passive_ceiling,
        }
    target = floor_to_increment(min(edge_ceiling, passive_ceiling, _d(max_price)), _d(increment))
    if target < _d(min_price) or target <= current:
        return {
            "action": "HOLD_OR_CANCEL",
            "filled_outcome": filled,
            "missing_outcome": missing,
            "reason": "NO_SAFE_PRICE_IMPROVEMENT",
            "target_price": current,
            "edge_ceiling": edge_ceiling,
            "passive_ceiling": passive_ceiling,
            "final_edge": completed_pair_edge(filled_price=filled_price, complement_price=current),
        }
    final_edge = completed_pair_edge(filled_price=filled_price, complement_price=target)
    if final_edge < _d(min_final_edge):
        raise AssertionError("dynamic complement target violated edge floor")
    return {
        "action": "REPRICE_MAKER_COMPLEMENT",
        "filled_outcome": filled,
        "missing_outcome": missing,
        "target_price": target,
        "prior_price": current,
        "price_improvement": target - current,
        "edge_ceiling": edge_ceiling,
        "passive_ceiling": passive_ceiling,
        "final_edge": final_edge,
        "min_final_edge": _d(min_final_edge),
    }
