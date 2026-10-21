"""Bounded defensive completion for an unmatched first leg.

WHAT THIS IS FOR, AND WHAT IT IS NOT. 2315z filled a first leg at own 0.37
for $1.2543 and never obtained its complement; the residual settled
worthless and cost more than both winning pairs of that campaign combined.
This module decides whether to pay to get out of such a leg.

IT IS NOT AN EDGE, AND MUST NEVER BE SOLD AS ONE. At the moment of the
2315z decision the bid was 0.20, so the market priced the position at
0.20 * 3.39 = $0.6780 and the sale would have netted $0.5865. The whole
difference, -$0.0915, is the taker fee. A defensive exit converts an
uncertain loss into a certain one and pays a fee for the conversion. Its
justification is tail control: at $5 aggregate across eight windows one
stranded leg can consume the daily halt and block every remaining window.
`decide()` therefore reports `improves_expected_value: False` always, so no
caller and no reader can mistake variance reduction for profit.

THE TRIGGER IS TIME, NOT DISTANCE, AND DELIBERATELY SO. The contrast
between 2300z (frozen complement 4-8 ticks away, filled at 0.71, +$0.1875)
and 2315z (18-27 ticks away, never filled, -$1.2543) suggests distance
predicts completion. That is TWO OBSERVATIONS and no threshold is fitted to
it here. Instead the guard is the deterministic end of completion
eligibility: defensive action is considered only in the last
`DEFENSIVE_ARM_S` seconds before the completion cutoff the venue clock
already defines.

WHAT THE CONSERVATIVE CHOICE COSTS, STATED. Exiting 2315z at the first
opportunity (23:21:50, bid 0.20) would have saved $0.5865. Waiting for the
guard to open saves ~$0.28-0.32 instead. We give up most of the theoretical
saving to buy the guarantee that we never pre-empt a complement that still
has runway -- which, in 2300z, was worth +$0.1875 and would have been
destroyed by an eager rule.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional


#: the pair cost a defensive exit may not exceed. The profitable ceiling is
#: 0.96; this is the separate, looser bound that applies ONLY to unwinding.
#:
#: WHERE 1.20 COMES FROM, which is the economics and not these two windows.
#: Holding an unmatched leg risks its ENTIRE cost. Exiting at pair cost P
#: loses (P - 1.00). Exiting is therefore the bounded choice whenever
#: P < 1.00 + first_leg_cost. The 0.96 entry ceiling means a first leg can
#: cost at most 0.96, so the weakest case this can face is a very cheap
#: first leg -- and `decide()` re-derives that bound per position rather
#: than trusting this constant alone.
DEFENSIVE_PAIR_CEILING = D("1.20")

#: campaign-wide realized defensive loss budget, inside the $5 daily halt.
DEFENSIVE_LOSS_BUDGET_USD = D("2.00")

#: how long before the completion cutoff defensive action may be considered.
#: 60s of a 900s window. Short on purpose: see the module docstring.
DEFENSIVE_ARM_S = 60.0


#: THE FEATURE FLAG. Absent, empty, misspelled or unreadable is OFF. Only
#: the exact strings below enable it, so a typo cannot half-arm a policy
#: that spends money, and the default on every deployment is disabled.
DEFENSIVE_ENABLED_ENV = "L5_DEFENSIVE_COMPLETION"
_TRUE = ("1", "yes", "true", "on")


def enabled() -> bool:
    import os
    try:
        return (os.getenv(DEFENSIVE_ENABLED_ENV) or "").strip().lower() \
            in _TRUE
    except BaseException:                                  # noqa: BLE001
        return False


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def arm_window_open(now_t: float, window_start: float,
                    complete_cutoff_s: float,
                    arm_s: float = DEFENSIVE_ARM_S) -> bool:
    """Is `now` inside the last `arm_s` seconds of completion eligibility?

    Closed at the cutoff: past it the venue will not accept a completing
    order anyway, and claiming otherwise would invent a fill.
    """
    try:
        off = float(now_t) - float(window_start)
        cut = float(complete_cutoff_s)
    except (TypeError, ValueError):
        return False
    return (cut - float(arm_s)) <= off < cut


def decide(*,
           now_t: float,
           window_start: float,
           complete_cutoff_s: float,
           unmatched_qty: Any,
           first_leg_cost: Any,
           exit_price: Any,
           displayed_depth: Any,
           taker_fee_per_contract: Any,
           defensive_spent_usd: Any = D(0),
           defensive_pending: bool = False,
           window_settled: bool = False,
           resting_complement: Optional[Mapping[str, Any]] = None,
           pair_ceiling: D = DEFENSIVE_PAIR_CEILING,
           loss_budget_usd: D = DEFENSIVE_LOSS_BUDGET_USD,
           arm_s: float = DEFENSIVE_ARM_S,
           ) -> Dict[str, Any]:
    """Should we pay to unwind this unmatched leg, and at what bounded cost?

    `exit_price` is the OWN price of the complementary side as the book
    actually shows it -- the executable price, not a maker price we hope to
    rest at. Every refusal is named. Nothing here submits, cancels or
    promises a fill.
    """
    out: Dict[str, Any] = {
        "act": False, "reason": None,
        # never, under any inputs. See the module docstring.
        "improves_expected_value": False,
        "labels": ("a defensive exit bounds a tail, it does not create edge: "
                   "at the touch it is expected-value neutral before fees "
                   "and negative by exactly the fee. A displayed size is NOT "
                   "a fill and this result never reports a position as "
                   "hedged -- only a venue-confirmed fill does that."),
    }

    if not enabled():
        out["reason"] = "DEFENSIVE_DISABLED"
        return out

    # ONE OUTSTANDING ORDER. A defensive order already working owns the
    # slot; a second would breach the limit and could double the position
    # if both fill.
    if defensive_pending:
        out["reason"] = "DEFENSIVE_ALREADY_PENDING"
        return out

    # SETTLEMENT WHILE AN ORDER IS PENDING. Once the window is terminal the
    # venue will not complete anything; acting here would invent a fill and
    # the residual is already decided.
    if window_settled:
        out["reason"] = "WINDOW_ALREADY_SETTLED"
        return out

    qty = _d(unmatched_qty)
    leg = _d(first_leg_cost)
    px = _d(exit_price)
    fee = _d(taker_fee_per_contract)
    spent = _d(defensive_spent_usd)
    if qty is None or leg is None or px is None or fee is None \
            or spent is None:
        out["reason"] = "DEFENSIVE_INPUT_UNREADABLE"
        return out
    if qty <= 0:
        out["reason"] = "NO_UNMATCHED_INVENTORY"
        return out
    if leg <= 0 or px <= 0 or fee < 0 or spent < 0:
        out["reason"] = "DEFENSIVE_INPUT_IMPLAUSIBLE"
        return out

    if not arm_window_open(now_t, window_start, complete_cutoff_s, arm_s):
        out["reason"] = "OUTSIDE_DEFENSIVE_ARM_WINDOW"
        return out

    # A PROFITABLE COMPLEMENT STILL RESTING OWNS THE POSITION. It may fill,
    # and in 2300z it did. It is displaced only by a cancellation the venue
    # has CONFIRMED -- an unconfirmed cancel plus a defensive order is how
    # both fill and the position doubles instead of closing.
    if resting_complement:
        if not bool(resting_complement.get("cancel_confirmed")):
            out["reason"] = "RESTING_COMPLEMENT_NOT_CONFIRMED_CANCELLED"
            out["resting_complement"] = {
                "venue_order_id": resting_complement.get("venue_order_id"),
                "cancel_confirmed": False,
            }
            return out

    pair_cost = leg + px
    fees_usd = qty * fee
    # what the two courses can cost us, in dollars, worst case each
    hold_downside = qty * leg                 # our side loses: lose it all
    exit_downside = qty * (pair_cost - D(1)) + fees_usd

    out.update({
        "qty": str(qty), "first_leg_cost": str(leg),
        "exit_price": str(px), "pair_cost": str(pair_cost),
        "fees_usd": str(fees_usd),
        "hold_downside_usd": str(hold_downside),
        "exit_downside_usd": str(exit_downside),
    })

    # A CHECK THAT WAS HERE AND IS GONE, because it could never fire and
    # reading it would have implied a protection that does not exist.
    # "Exit only while pair_cost < 1 + first_leg_cost" reduces, after
    # substituting pair_cost = first_leg_cost + exit_price, to
    # `exit_price < 1.00` -- true of every valid binary price. Worst-case
    # comparison ALWAYS favours exiting: holding risks the whole leg while
    # exiting recovers (1 - exit_price) per contract, so the exit wins
    # whenever that recovery beats the fee. `DOES_NOT_REDUCE_BOUNDED_
    # DOWNSIDE` below still states the comparison and will refuse the
    # fee-dominated corner, but it is NOT the binding protection and must
    # not be mistaken for one.
    #
    # WHAT ACTUALLY PROTECTS US is therefore the arm window above (do not
    # pre-empt a complement that still has runway), this ceiling, the
    # budget, and the depth test. The 2300z case is caught by the clock,
    # not by any economic comparison -- every economic comparison would
    # have told us to exit, and exiting would have cost -$0.5137.
    if pair_cost > pair_ceiling:
        out["reason"] = "DEFENSIVE_PAIR_CEILING"
        return out
    if exit_downside >= hold_downside:
        out["reason"] = "DOES_NOT_REDUCE_BOUNDED_DOWNSIDE"
        return out

    if spent + exit_downside > loss_budget_usd:
        out["reason"] = "DEFENSIVE_LOSS_BUDGET"
        out["budget_remaining_usd"] = str(loss_budget_usd - spent)
        return out

    # LIQUIDITY. A displayed size is the only evidence we have and it is not
    # a promise; unreadable depth refuses rather than assuming the book is
    # deep. The ratio is reported so a reader can judge the claim.
    depth = _d(displayed_depth)
    if depth is None:
        out["reason"] = "DEPTH_UNREADABLE"
        return out
    if depth < qty:
        out["reason"] = "INSUFFICIENT_DISPLAYED_DEPTH"
        out["depth_ratio"] = str(depth / qty) if qty else None
        return out
    out["depth_ratio"] = str((depth / qty).quantize(D("0.1")))

    out["act"] = True
    out["reason"] = "DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"
    out["saves_vs_holding_usd"] = str(hold_downside - exit_downside)
    return out
