"""Pure q=10 dynamic-completion policy for Gemini short-crypto reward probes.

No venue mutation. The policy spends remaining pair edge more aggressively as
expiry approaches, while keeping the missing-side order maker-only. Rewards and
rebates are excluded from the trade-edge floor; they may be analyzed separately.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

Q = D("10")
ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")


def _d(v: Any) -> D:
    return D(str(v))


def floor_to_increment(v: D, inc: D = CENT) -> D:
    v, inc = _d(v), _d(inc)
    return (v / inc).to_integral_value(rounding=ROUND_FLOOR) * inc


def time_dependent_total_profit_floor(seconds_to_expiry: float) -> D:
    """Required q10 complete-set trade profit, excluding all incentives.

    >120s: preserve at least $0.50
    60-120s: preserve at least $0.25
    20-60s: preserve at least $0.00
    <20s: maker completion policy expires; caller should compare explicit unwind
    alternatives instead of blindly buying the complement.
    """
    s = float(seconds_to_expiry)
    if s > 120:
        return D("0.50")
    if s > 60:
        return D("0.25")
    if s >= 20:
        return D("0.00")
    raise ValueError("inside 20s use explicit lowest-cost risk exit, not maker completion")


def completed_pair_profit_q10(*, filled_price: D, complement_price: D) -> D:
    filled_price, complement_price = _d(filled_price), _d(complement_price)
    if not (ZERO < filled_price < ONE and ZERO < complement_price < ONE):
        raise ValueError("prices must be inside (0,1)")
    fees = gemini_predictions_maker_fee(filled_price, Q) + gemini_predictions_maker_fee(complement_price, Q)
    return Q * (ONE - filled_price - complement_price) - fees


def maker_passive_ceiling(*, missing_outcome: str, yes_bid: D, yes_ask: D, increment: D = CENT) -> D:
    missing = str(missing_outcome).strip().upper()
    yes_bid, yes_ask, increment = _d(yes_bid), _d(yes_ask), _d(increment)
    if missing == "YES":
        raw = yes_ask - increment
    elif missing == "NO":
        raw = (ONE - yes_bid) - increment
    else:
        raise ValueError("missing_outcome must be YES or NO")
    return floor_to_increment(raw, increment)


def max_profit_safe_complement_price(*, filled_price: D, min_total_profit: D,
                                     increment: D = CENT, min_price: D = MIN_PRICE,
                                     max_price: D = MAX_PRICE) -> D | None:
    p = floor_to_increment(_d(max_price), _d(increment))
    while p >= _d(min_price):
        if completed_pair_profit_q10(filled_price=_d(filled_price), complement_price=p) >= _d(min_total_profit):
            return p
        p -= _d(increment)
    return None


def completion_plan(*, filled_outcome: str, filled_price: D, current_complement_price: D,
                    yes_bid: D, yes_ask: D, seconds_to_expiry: float,
                    increment: D = CENT) -> dict[str, Any]:
    floor = time_dependent_total_profit_floor(seconds_to_expiry)
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    missing = "NO" if filled == "YES" else "YES"
    edge_ceiling = max_profit_safe_complement_price(
        filled_price=_d(filled_price), min_total_profit=floor, increment=_d(increment)
    )
    passive = maker_passive_ceiling(missing_outcome=missing, yes_bid=_d(yes_bid), yes_ask=_d(yes_ask), increment=_d(increment))
    current = _d(current_complement_price)
    if edge_ceiling is None:
        return {"action":"HOLD","reason":"NO_PRICE_MEETS_PROFIT_FLOOR","missing_outcome":missing,"profit_floor":floor}
    target = floor_to_increment(min(edge_ceiling, passive, MAX_PRICE), _d(increment))
    if target <= current:
        return {"action":"HOLD","reason":"NO_SAFE_MAKER_IMPROVEMENT","missing_outcome":missing,
                "target_price":current,"profit_floor":floor,
                "projected_total_profit":completed_pair_profit_q10(filled_price=_d(filled_price), complement_price=current)}
    projected = completed_pair_profit_q10(filled_price=_d(filled_price), complement_price=target)
    if projected < floor:
        raise AssertionError("completion target violated time-dependent profit floor")
    return {"action":"REPRICE_MAKER_COMPLEMENT","missing_outcome":missing,"target_price":target,
            "prior_price":current,"profit_floor":floor,"projected_total_profit":projected,
            "edge_ceiling":edge_ceiling,"passive_ceiling":passive}
