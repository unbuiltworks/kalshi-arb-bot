"""GEMINI_PAIR_POLICY_V1 -- the mechanism, with the probability model removed.

P8-A's decomposition of the EV controller found that its decisions were almost
entirely reproduced by two pieces of arithmetic that need no probability model
at all: 89.9% of acquisitions by a projected-pair-cost test and 96.0% of
completions by a realized-pair-cost test. The imported `p_win` curve moved
only ~10% and ~4% of decisions respectively, and that curve is FALSIFIED on
Gemini. So this module states the arithmetic as a policy in its own right and
imports nothing from `ev_policy`.

    ACQUIRE a fresh or extending leg at p   iff   p + q_hat  <=  acq_max
    COMPLETE an unpaired leg bought at a    iff   a + p      <=  comp_max

`q_hat` is the mean price at which the COMPLEMENT side has printed SO FAR in
this contract -- causal, from the tape already seen, never from later prints.

WHEN THE COMPLEMENT HAS NEVER PRINTED, `q_hat` is None and the answer is SKIP.
That is a deliberate refusal rather than an omission. Any fallback -- 1-p, a
constant, the other side's own price -- is an assumption that a complement
exists at a price nobody has paid, and that assumption is exactly how a cheap
first leg turns into an uncompleted directional bet. The cost of the refusal
is measured (`skipped_no_qhat`) rather than hidden.

THE THRESHOLDS ARE NOT CONSTANTS OF NATURE. 0.95 and 1.20 were the values that
best APPROXIMATED the old controller; that is a statement about the old
controller, not about the market. They are parameters here and the response
surface over them is mapped on development data only.

NO REBATE TERM. NO OUTCOME. NO CLOCK BEYOND THE PRINT'S OWN TIMESTAMP.
"""
from __future__ import annotations

from fractions import Fraction as F
from typing import Any, Dict, Mapping, Optional

LABEL = "GEMINI_PAIR_POLICY_V1"

YES, NO = "YES", "NO"
ACQUIRE, COMPLETE, SKIP = "ACQUIRE", "COMPLETE", "SKIP"


def decide(*, yes_qty: int, no_qty: int,
           avg_yes: Optional[F], avg_no: Optional[F],
           offered_side: str, price: F,
           q_hat: Optional[F],
           acq_max: F, comp_max: F,
           min_leg: Optional[F] = None,
           late: bool = False,
           comp_late: Optional[F] = None) -> Dict[str, Any]:
    """What to do with the unit the tape has just made buyable.

    `late` and `comp_late` implement the deadline half of a WAIT rule: once a
    contract is inside its final stretch, the completion ceiling may be
    relaxed to `comp_late`, because from that point on refusing a complement
    is no longer waiting for a better one -- it is choosing to settle
    directional. Omit both and no relaxation happens.
    """
    reduces = ((offered_side == NO and yes_qty > no_qty)
               or (offered_side == YES and no_qty > yes_qty))
    if reduces:
        leg = avg_yes if offered_side == NO else avg_no
        ceiling = comp_max
        if late and comp_late is not None:
            ceiling = max(comp_max, comp_late)
        pair = None if leg is None else leg + price
        ok = pair is not None and pair <= ceiling
        return {"kind": COMPLETE, "action": COMPLETE if ok else SKIP,
                "pair_cost": pair, "ceiling": ceiling, "late": bool(late),
                "why": "realized pair cost against the completion ceiling"}
    if min_leg is not None and price < min_leg:
        #: A FLOOR ON WHAT A LEG MAY COST, added after the DEV residual study.
        #: The projected-pair test alone is happy to open a 0.12 leg on a
        #: promise of a 0.78 complement, and when that complement never comes
        #: the position is not a cheap pair, it is a long-shot directional
        #: bet. This refuses the cheapest legs outright; whether it is worth
        #: its lost volume is an empirical question, not an assumption.
        return {"kind": ACQUIRE, "action": SKIP, "projected_pair": None,
                "ceiling": acq_max, "no_qhat": False, "below_min_leg": True,
                "why": "leg is cheaper than the minimum a leg may cost"}
    if q_hat is None:
        return {"kind": ACQUIRE, "action": SKIP, "projected_pair": None,
                "ceiling": acq_max, "no_qhat": True,
                "why": "the complement side has never printed; its price is "
                       "unknown and is not assumed"}
    proj = price + q_hat
    return {"kind": ACQUIRE, "action": ACQUIRE if proj <= acq_max else SKIP,
            "projected_pair": proj, "ceiling": acq_max, "no_qhat": False,
            "why": "projected pair cost against the acquisition ceiling"}
