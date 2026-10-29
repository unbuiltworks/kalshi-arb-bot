"""What a capital or inventory limit actually refused, priced.

WHY THIS EXISTS. The engine already records THAT a submission was refused:
`CASH_COMMITMENT`, `RESIDUAL_ABOVE_CAP`, `MAX_LIVE_ORDERS` and the exposure
refusals all surface in `L5_LIVE_DECISION`. What no record carries is what
the refusal COST, and without that a limit cannot be argued about. A limit
that refuses nothing profitable is free; a limit that refuses matched
surplus is paying for safety with throughput, and the owner is entitled to
see the price before being asked to change it.

FOUR NUMBERS PER REFUSAL, deliberately separate because they answer
different questions:

  expected_matched_surplus_usd   what the PAIR would net if BOTH legs filled
                                 -- the upside the limit declined
  additional_obligation_usd      what the cash gate would have charged, on
                                 its own convention: a dollar a share for a
                                 BUY_SHORT leg, q * px otherwise
  completion_distance_ticks      how far the complement's touch sits from a
                                 permissible completion price. EVIDENCE
                                 about reachability, NOT a probability
  downside_if_stranded_usd       the cash lost if this leg fills and never
                                 completes -- the reason the limit exists

NOTHING HERE IS A PROBABILITY, A FILL OR A REALISED FIGURE. The surplus is
conditional on two fills that the venue has not granted and that no book
snapshot can promise. `completion_distance_ticks` is a distance on a price
grid; the venue publishes no per-order queue position, so a reachable price
is not a fill. These are inputs to a decision about limits, not a forecast.
"""
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.l5_two_sided_probe import (MAKER_THETA, _d, completion_ceiling,
                                    fee, touch_own_price, wire_price)

#: the engine's own refusal strings, mapped to the limit the owner sets
BLOCKED_BY = {
    "CASH_COMMITMENT": "CASH_ENVELOPE",
    "RESIDUAL_CAP": "RESIDUAL_LIMIT",
    "RESIDUAL_ABOVE_CAP": "RESIDUAL_LIMIT",
    "MAX_LIVE_ORDERS": "ORDER_COUNT",
    "ACCOUNT_EXPOSURE_REFUSED": "EXPOSURE_CAP",
    "EXPOSURE_UNREADABLE": "EXPOSURE_CAP",
    "EXPOSURE_ENUMERATION_FAILED": "EXPOSURE_CAP",
}


def classify(reason: Any) -> Optional[str]:
    """Which owner-set limit a refusal belongs to, or None if it is not one.

    Matched on the PREFIX, because the engine's reasons carry their numbers
    (`CASH_COMMITMENT:3.1>3.0`). A reason this does not recognise returns
    None rather than a guess: an unclassified refusal is not evidence about
    a limit and must not be counted as one.
    """
    r = str(reason or "")
    for k, v in BLOCKED_BY.items():
        if r == k or r.startswith(k + ":"):
            return v
    return None


def capital_block_record(*, reason: Any, side: str, price: Any, qty: Any,
                         bid: Any, ask: Any, tick: Any = "0.01",
                         held_qty: Any = 0, held_cost_usd: Any = 0,
                         deficient_qty: Any = 0,
                         deficient_cost_usd: Any = 0,
                         residual_budget_usd: Any = None,
                         is_buy_short: Optional[bool] = None
                         ) -> Optional[Dict[str, Any]]:
    """Price one refusal, or None when it is not an owner-set limit.

    `side` is the leg that was refused and `price` its own-outcome price.
    `held_*` is what the market already holds. Unreadable inputs leave their
    field None rather than guessing: a missing number is reported as missing,
    because a limit argued from a fabricated figure is worse than one argued
    from none.
    """
    blocked = classify(reason)
    if blocked is None:
        return None
    q = _d(qty)
    px = _d(price)
    b, a = _d(bid), _d(ask)
    tk = _d(tick) or D("0.01")
    out: Dict[str, Any] = {
        "blocked_by": blocked, "reason": str(reason)[:120],
        "side": str(side), "own_price": (None if px is None else str(px)),
        "qty": (None if q is None else str(q)),
    }
    # THE CASH GATE'S OWN CONVENTION, not a restatement of it: a BUY_SHORT
    # leg is charged a dollar a share whatever its price, which is why a
    # dearer DOWN quote costs the envelope nothing extra.
    short = (str(side).upper() == "DOWN") if is_buy_short is None \
        else bool(is_buy_short)
    out["is_buy_short"] = bool(short)
    out["additional_obligation_usd"] = (
        None if q is None else str(q if short else
                                   (q * px if px is not None else q)))
    # the cash genuinely at risk if this leg fills alone and never completes
    out["downside_if_stranded_usd"] = (
        None if (q is None or px is None) else str(q * px))
    # what the PAIR would net if both legs filled, the complement taken at
    # the touch it would have to pay now
    comp_side = "DOWN" if str(side).upper() == "UP" else "UP"
    touch = (None if (b is None or a is None)
             else touch_own_price(comp_side, bid=b, ask=a))
    if q is not None and px is not None and touch is not None:
        pair = px + touch
        f = fee(MAKER_THETA, q, px) + fee(MAKER_THETA, q, touch)
        out["complement_touch_own_px"] = str(touch)
        out["pair_cost_if_both_fill"] = str(pair)
        out["expected_matched_surplus_usd"] = str(q * (D(1) - pair) - f)
    else:
        out["complement_touch_own_px"] = (None if touch is None
                                          else str(touch))
        out["pair_cost_if_both_fill"] = None
        out["expected_matched_surplus_usd"] = None
    # REACHABILITY EVIDENCE, not a probability. How far the complement's
    # touch sits from the dearest price a completion of this inventory would
    # be permitted to pay.
    ceil = None
    if _d(held_qty) and residual_budget_usd is not None:
        ceil = completion_ceiling(
            held_side=str(side), held_qty=held_qty,
            held_cost_usd=held_cost_usd, deficient_qty=deficient_qty,
            deficient_cost_usd=deficient_cost_usd,
            complete_qty=(q if q is not None else held_qty),
            residual_budget_usd=residual_budget_usd, tick=tk)
    out["completion_ceiling"] = (None if ceil is None else str(ceil))
    out["completion_distance_ticks"] = (
        None if (ceil is None or touch is None) else str((touch - ceil) / tk))
    out["wire_price"] = (None if px is None else str(wire_price(side, px)))
    out["labels"] = (
        "REPORTING ONLY. expected_matched_surplus_usd is conditional on BOTH "
        "legs filling and is not a forecast, a probability or a realised "
        "figure. completion_distance_ticks is a distance on the price grid: "
        "the venue publishes no per-order queue position, so a reachable "
        "price is not a fill. POSITIVE completion_distance_ticks means the "
        "complement's touch sits ABOVE the ceiling and no permissible "
        "completion is reachable there; NEGATIVE is headroom under it. "
        "additional_obligation_usd follows the cash gate's own convention, "
        "a dollar a share for a BUY_SHORT leg.")
    return out
