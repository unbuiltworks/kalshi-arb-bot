"""Fee-safe matched-pair cost, under the FROZEN effective-dated schedule.

ONE SCHEDULE, NOT A SECOND COPY. Every fee number here comes from
`src.pmus_fee_schedule`, whose fingerprint is
`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629` and whose
`assert_frozen()` is called before any arithmetic. Nothing in this module
re-derives a coefficient, re-implements the formula, or hard-codes a theta.
A duplicate fee table would be the same class of mistake as the duplicate
serializer that killed Attempt 5.

WHAT "FEE-SAFE" MEANS HERE, STATED PRECISELY AND CONSERVATIVELY.

Both legs are submitted post-only (`participateDontInitiate`), so the venue
cannot fill either as a taker, and the maker side of the schedule pays a
REBATE rather than charging a fee. A cost model built on that is correct but
UNVERIFIED-OPTIMISTIC: it assumes both the post-only guarantee and the role
classification hold. This module instead prices the pair at the TAKER
coefficient on BOTH legs:

    fee_safe_pair_cost_per_contract
        = p_first + p_complement
        + taker_fee(p_first) + taker_fee(p_complement)
        - 0                              <- the rebate, counted as ZERO

That is strictly more expensive than anything the venue can actually charge
a post-only pair, so a pair that passes this gate is fee-safe under every
role outcome including the one where our post-only assumption is wrong.

THE COST OF THAT CONSERVATISM IS REAL AND IS NOT HIDDEN. At p around 0.5 the
taker coefficient 0.0695 costs about $0.0174 per contract per leg, so the
<= $0.96 ceiling becomes roughly p_first + p_complement <= $0.925 in raw
prices. The gate is therefore TIGHTER than "prices sum to 0.96", and will
refuse pairs a maker-priced model would allow. That is the direction we want
for a first matched-pair probe; `headroom_report()` states the gap in cents
so a reviewer can see exactly what the conservatism costs.

THE REBATE IS ZERO BY STRUCTURE, NOT BY CHOICE. At the protocol's 1.00-share
size, round_half_even(0.0125 * C * p * (1-p), $0.01) is $0.00 -- the hard
cent floor. `maker_rebate_estimate()` returns that $0.00 and labels it
ESTIMATED; it is reported for the record and is NEVER an input to a gate.
A rebate is income only once the venue has credited it as a negative
`commissionNotionalCollected` on our own execution.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, Optional

from src import pmus_fee_schedule as FS
from analysis.l5.stage6 import bounds as B

CENT = D("0.01")


def _when(fill_ts: Optional[str]) -> Optional[dt.datetime]:
    return FS.parse_ts(fill_ts) if fill_ts else None


def taker_cost_per_contract(price: Any,
                            fill_ts: Optional[str] = None) -> D:
    """The TAKER fee for ONE contract at `price`, under the schedule in
    force at `fill_ts` (now if absent). Un-rounded to 4dp, because this is
    an intermediate in a per-contract sum, not a charged amount."""
    FS.assert_frozen()
    return FS.taker_fee(D(str(price)), D("1"), when=_when(fill_ts),
                        cents=False)


def maker_rebate_estimate(price: Any, contracts: Any,
                          fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """ESTIMATED maker rebate, cent-rounded exactly as the venue publishes.

    Reported, never gated on. At this protocol's size it is $0.00 because
    of the cent floor, and the answer says so.
    """
    FS.assert_frozen()
    c = D(str(contracts))
    amt = FS.maker_rebate(D(str(price)), c, when=_when(fill_ts), cents=True)
    return {
        "usd": str(amt),
        "contracts": str(c),
        "below_cent_floor": c < B.REBATE_CENT_FLOOR_SHARES,
        "cent_floor_shares": str(B.REBATE_CENT_FLOOR_SHARES),
        "structurally_zero_at_this_size": amt == 0,
        "counted_in_the_pair_cost_gate": False,
        "label": ("ESTIMATED from the published formula. NOT income. A "
                  "rebate is income only once the venue credits it as a "
                  "negative commissionNotionalCollected on our own "
                  "execution."),
        "schedule_fingerprint": FS.FINGERPRINT,
    }


def fee_safe_pair_cost(first_price: Any, complement_price: Any, *,
                       fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """Per-matched-contract fee-safe cost, and whether it clears $0.96."""
    FS.assert_frozen()
    p1, p2 = D(str(first_price)), D(str(complement_price))
    f1 = taker_cost_per_contract(p1, fill_ts)
    f2 = taker_cost_per_contract(p2, fill_ts)
    raw = p1 + p2
    total = raw + f1 + f2
    return {
        "first_price": str(p1),
        "complement_price": str(p2),
        "raw_pair_cost": str(raw),
        "taker_fee_first": str(f1),
        "taker_fee_complement": str(f2),
        "rebate_credited": "0",
        "fee_safe_pair_cost": str(total),
        "ceiling": str(B.PAIR_COST_CEILING),
        "clears_ceiling": total <= B.PAIR_COST_CEILING,
        "locked_gross_per_contract": str(D("1.00") - total),
        "fee_basis": ("TAKER coefficient on BOTH legs even though both are "
                      "post-only -- strictly conservative"),
        "effective_date_ambiguous": FS.date_conflict_window(_when(fill_ts)),
        "schedule_fingerprint": FS.FINGERPRINT,
    }


def max_complement_price(first_fill_vwap: Any, *,
                         fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """The HIGHEST complement price that still clears the fee-safe ceiling,
    snapped DOWN to the venue's 0.01 tick.

    Solved by search over the 0.01 grid rather than algebraically, because
    the fee is a rounded function of the complement price itself: an
    algebraic inverse would be an approximation of the schedule, and an
    approximation of the schedule is a second schedule.
    """
    FS.assert_frozen()
    p1 = D(str(first_fill_vwap))
    best: Optional[D] = None
    # walk DOWN from the venue ceiling; the first grid price that clears is
    # the maximum, and the sum is monotone in p2 over the feasible region.
    px = B.VENUE_MAX_PRICE
    while px >= B.VENUE_MIN_PRICE:
        if fee_safe_pair_cost(p1, px, fill_ts=fill_ts)["clears_ceiling"]:
            best = px
            break
        px = (px - B.VENUE_TICK).quantize(CENT)
    feasible = best is not None and best >= B.VENUE_MIN_PRICE
    return {
        "first_fill_vwap": str(p1),
        "max_complement_price": (str(best) if feasible else None),
        "feasible": feasible,
        "tick": str(B.VENUE_TICK),
        "snapped": "DOWN_TO_TICK",
        "why_searched_not_solved": ("the fee is a rounded function of the "
                                    "complement price, so an algebraic "
                                    "inverse would be a second schedule"),
        "refusal": (None if feasible else
                    "NO_FEE_SAFE_COMPLEMENT_PRICE_EXISTS: the first leg "
                    "filled too expensively for any complement on the "
                    "venue grid to clear the $%s ceiling"
                    % B.PAIR_COST_CEILING),
    }


def headroom_report(first_price: Any = B.MAX_FIRST_LEG_PRICE,
                    fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """What the taker-on-both-legs conservatism costs, in cents.

    Stated so the choice is visible rather than buried: this is the band of
    complement prices that a maker-priced model would allow and this gate
    refuses.
    """
    FS.assert_frozen()
    p1 = D(str(first_price))
    conservative = max_complement_price(p1, fill_ts=fill_ts)
    raw_allowed = (B.PAIR_COST_CEILING - p1).quantize(CENT,
                                                      rounding=ROUND_FLOOR)
    cons = (D(conservative["max_complement_price"])
            if conservative["feasible"] else None)
    return {
        "first_price": str(p1),
        "complement_ceiling_if_fees_ignored": str(raw_allowed),
        "complement_ceiling_fee_safe": (str(cons) if cons is not None
                                        else None),
        "conservatism_cost_cents": (
            int(((raw_allowed - cons) / CENT).quantize(
                D("1"), rounding=ROUND_CEILING))
            if cons is not None else None),
        "direction": ("the gate REFUSES pairs a maker-priced model would "
                      "allow. Intended for a first matched-pair probe."),
    }


def pair_gate(first_fill_vwap: Any, complement_price: Any, *,
              fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """The decision: may this complement price be quoted at all?

    `ok` False means NO complement order. There is no override, no
    "close enough", and no near-par branch -- near-$1 pair tolerance is
    LOCKED later-incentive-era behaviour and is not implemented here at
    all, so it cannot be switched on by a flag.
    """
    econ = fee_safe_pair_cost(first_fill_vwap, complement_price,
                              fill_ts=fill_ts)
    refusals = []
    if not econ["clears_ceiling"]:
        refusals.append("PAIR_COST_ABOVE_CEILING:%s>%s"
                        % (econ["fee_safe_pair_cost"], B.PAIR_COST_CEILING))
    p2 = D(str(complement_price))
    if (p2 / B.VENUE_TICK) % 1 != 0:
        refusals.append("COMPLEMENT_PRICE_OFF_TICK:%s" % p2)
    if not (B.VENUE_MIN_PRICE <= p2 <= B.VENUE_MAX_PRICE):
        refusals.append("COMPLEMENT_PRICE_OFF_VENUE_RANGE:%s" % p2)
    return {"ok": not refusals, "refusals": refusals, "economics": econ,
            "near_par_branch_exists": False}
