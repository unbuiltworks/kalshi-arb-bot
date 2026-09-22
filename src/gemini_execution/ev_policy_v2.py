"""GEMINI_EV_POLICY_V2 -- V1 with the side orientation corrected, nothing else.

THE DEFECT, found in P8-A. `ev_policy.decide` computes

    acquire_value(price, state, offered_side)

and `acquire_value`'s third parameter is named `need_side`: it is the side we
would still have to BUY in order to pair. Passing the side being OFFERED makes
`p_complete` and `q_hat` describe the wrong half of the book. The acquisition
test stops being "this price plus the expected complement" and becomes roughly
"twice this price", because a side's own mean price is what it gets compared
against.

WHY V1 IS NOT EDITED. Every P7 and P8 arm was scored under V1. Correcting it in
place would silently restate those results, which is exactly the move the
programme's accounting rules forbid: a superseded number must remain readable.
V1 stays byte-compatible and keeps its own docstring warning; this module is
the corrected implementation and is run as a COMPARATOR, not as a continuation
of P8's tuning.

EVERYTHING ELSE IS IMPORTED, NOT COPIED. The residual-return table, `p_win`,
`MarketState`, `complete_value` and `acquire_value` are V1's own objects. The
only difference between the two policies is which side is looked up, so the
difference in their outputs cannot come from anywhere else.

NO REBATE TERM. NO OUTCOME. OFFLINE AND PURE.
"""
from __future__ import annotations

from fractions import Fraction as F
from typing import Any, Dict, Mapping

from src.gemini_execution.ev_policy import (        # noqa: F401
    COMPLETE, EXTEND, HOLD, NO, OPEN, RESIDUAL_RETURN, YES,
    MarketState, acquire_value, complete_value, p_win, residual_return,
)

LABEL = "GEMINI_EV_POLICY_V2"


def complement_of(side: str) -> str:
    """The leg that would have to be bought to pair a holding of `side`.

    Trivial, and it exists as a named function so that the orientation is
    testable on its own rather than being an inline expression nobody checks.
    """
    if side == YES:
        return NO
    if side == NO:
        return YES
    raise ValueError(f"not a side: {side!r}")


def decide(*, inventory: Mapping[str, Any], offered_side: str, price: F,
           state: MarketState) -> Dict[str, Any]:
    """V1's decision with the complement looked up on the correct side."""
    y = int(inventory.get("yes_qty") or 0)
    n = int(inventory.get("no_qty") or 0)
    ay = inventory.get("avg_yes")
    an = inventory.get("avg_no")

    reduces = (offered_side == NO and y > n) or (offered_side == YES and n > y)
    if reduces:
        leg_cost = F(str(ay)) if offered_side == NO else F(str(an))
        out = complete_value(leg_cost, price)
        out["kind"] = COMPLETE
        out["orientation"] = "v2"
        return out

    # THE ONE CORRECTED LINE: the side we will still need, not the side offered.
    need = complement_of(offered_side)
    out = acquire_value(price, state, need)
    out["kind"] = OPEN if (y + n) == 0 else EXTEND
    out["need_side"] = need
    out["orientation"] = "v2"
    return out
