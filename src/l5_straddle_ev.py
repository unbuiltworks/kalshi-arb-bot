"""What a cheap straddle rung is worth before it is placed. PURE.

WHY A GATE AT ALL. The straddle opened a rung whenever the clock and the
cap allowed, which is not a decision -- it is a reflex. Live, that reflex
produced 15 lone legs in a row, every one settling -0.16, against 2
completed pairs. Those losses are not evidence the edge is gone (0 for 15
at a 9.81% win rate happens about 21% of the time), but "the cap allows
it" is not a reason to buy something either. This module makes the rung
state its expected value, and `l5_btc5m_controller` refuses a
nonpositive one.

THE THREE WAYS A FILLED RUNG ENDS, and they are exclusive:

  NATURAL PAIR   the opposite cheap rung also fills. Pays 1 - (both
                 legs' cost): the best outcome this system has, about
                 +0.84 a share at 0.08 each.
  COMPLETION     we buy the other side later at the target pair cost.
                 Pays 1 - target_pair, about +0.17 at 0.83.
  LONE LEG       neither happens and it settles alone. Pays 1 - price if
                 that side wins, -price if not.

WHY THE PROBABILITIES ARE INPUTS WITH NO DEFAULTS. The one that matters
most -- P(this side wins | it filled at 0.08) -- is NOT `p_up` at entry
time. The entry is placed before any fill, and the leg that fills is the
one whose side COLLAPSED to 0.08 and must then recover. Measured over 503
shadow windows that is 9.81% against an 8% implied price, and no
probability available at entry time stands in for it. So every rate is
passed in from measurement, and an unset one refuses rather than being
guessed: a guessed rate here would manufacture the edge the gate exists
to test for.

FEES ARE CHARGED ON EVERY SHARE, win or lose, so they sit outside the
bracket. Rebates are NOT included: an uncredited rebate is not income,
and this module is given a net fee or nothing. On this venue the maker fee
is 0 and the maker rebate is Tier 1, which is ZERO -- Tier 2 needs
$10,000,000 of 30-day maker volume against this account's ~$38,880/month
(`pmus_fees`). So for a maker-only strategy the fee term is genuinely zero,
and it is NOT the cost that decides these quotes.

ADVERSE SELECTION IS, AND IT IS CHARGED ON THE LONE LEG.

`l5_btc5m_controller`'s own header states the value of a passive maker buy
at x: `p_s - x - adverse_selection_per_share`, from a measured ~6 cents a
share on a proxy. Its EDGE_GATED path charges that. The straddle path did
not, and with the maker fee at 0 the consequence was that EV reduced to
`p_pair_natural * (1 - pair_cost)`, which is positive for ANY p_pair_natural
above zero. MEASURED on a 0.52/0.46 quote: p_pair_natural = 0.001 -- one
tenth of one percent -- still admitted two legs. A gate that admits on any
epsilon is arithmetic, not a decision.

WHY THE LONE BRANCH AND ONLY THE LONE BRANCH. A natural pair is locked once
both legs fill: it pays 1 - (both costs) whatever the index then does, so
nothing can adversely select it. A completion is bought deliberately at a
known pair cost. The leg that ends up ALONE is the one we were filled on
because the price moved against us, and that is the whole of the owner's
stated failure mode -- "profitable completed pairs are overwhelmed by losses
on unmatched inventory". Charging it anywhere else would either double-count
or miss it.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

D = Decimal
ZERO = D("0")
ONE = D("1")


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def entry_ev(*, price: Any, other_price: Any, pair_target: Any,
             p_pair_natural: Any, p_complete: Any, p_lone_win: Any,
             fee_per_share: Any, adverse_per_share: Any) -> Dict[str, Any]:
    """Expected net dollars per share of a cheap rung, or why unknown.

    `other_price` is the opposite rung's price, because a natural pair
    costs BOTH legs and a 0.08/0.20 pair is a different trade from a
    0.08/0.08 one. `pair_target` is the completion's total pair cost.

    `adverse_per_share` is REQUIRED and has no default, like the three
    rates and for the same reason: a default of zero is the assumption
    that being filled tells us nothing, which is the assumption that
    emptied this gate. A caller that has not measured it must say so and
    be refused, not quietly credited the optimistic case.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, ev_per_share=None)

    px, opx = _dec(price), _dec(other_price)
    tgt = _dec(pair_target)
    fee = _dec(fee_per_share)
    adv = _dec(adverse_per_share)
    pn, pc, pw = (_dec(p_pair_natural), _dec(p_complete), _dec(p_lone_win))
    if px is None or not (ZERO < px < ONE):
        return no("PRICE_UNREADABLE")
    if opx is None or not (ZERO < opx < ONE):
        return no("OTHER_PRICE_UNREADABLE")
    if fee is None or fee < ZERO:
        return no("FEE_UNREADABLE")
    if adv is None or adv < ZERO:
        #: UNSET IS REFUSED, not read as zero. Zero adverse selection is a
        #: claim about the venue, and it is the claim that made every
        #: book-relative quote admissible.
        return no("ADVERSE_UNREADABLE")
    for name, v in (("P_PAIR_NATURAL", pn), ("P_COMPLETE", pc),
                    ("P_LONE_WIN", pw)):
        if v is None or not (ZERO <= v <= ONE):
            #: NO DEFAULT. A guessed rate manufactures the edge.
            return no("%s_NOT_SET" % name)
    if pn + pc > ONE:
        #: the three endings are exclusive, so these cannot sum past one.
        return no("PROBABILITIES_INCONSISTENT",
                  p_pair_natural=str(pn), p_complete=str(pc))
    #: a completion is only meaningful between the cheap leg's cost and 1
    if pc > ZERO:
        if tgt is None or not (px < tgt <= ONE):
            return no("PAIR_TARGET_UNREADABLE")
    p_lone = ONE - pn - pc

    pair_pay = ONE - (px + opx)
    comp_pay = ZERO if pc == ZERO else ONE - tgt
    #: the lone leg pays its fair bet LESS the adverse selection that put
    #: us in it. See the module docstring for why it is charged here only.
    lone_pay = pw * (ONE - px) - (ONE - pw) * px - adv
    ev = pn * pair_pay + pc * comp_pay + p_lone * lone_pay - fee
    return {"ok": True, "reason": None, "ev_per_share": ev,
            "breakdown": {
                "natural_pair": {"p": str(pn), "pays": str(pair_pay),
                                 "contributes": str(pn * pair_pay)},
                "completion": {"p": str(pc), "pays": str(comp_pay),
                               "contributes": str(pc * comp_pay)},
                "lone_leg": {"p": str(p_lone), "pays": str(lone_pay),
                             "contributes": str(p_lone * lone_pay),
                             "p_win": str(pw),
                             "adverse_per_share": str(adv)},
                "fee_per_share": str(fee)},
            "note": ("rebates are excluded: an uncredited rebate is not "
                     "income")}


def admits(result: Any, *, min_ev_per_share: Any = ZERO) -> Dict[str, Any]:
    """Whether `entry_ev` permits the rung. Nonpositive is refused.

    An UNKNOWN expected value is refused exactly as a negative one is:
    the gate exists so a rung is bought because it is worth buying, and
    "we could not tell" is not that.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return {"ok": False, "reason": (str((result or {}).get("reason"))
                                        if isinstance(result, dict)
                                        else "EV_UNAVAILABLE")}
    ev = _dec(result.get("ev_per_share"))
    floor = _dec(min_ev_per_share)
    if ev is None or floor is None:
        return {"ok": False, "reason": "EV_UNREADABLE"}
    if ev <= ZERO:
        return {"ok": False, "reason": "EV_NOT_POSITIVE",
                "ev_per_share": str(ev)}
    if ev < floor:
        return {"ok": False, "reason": "EV_BELOW_FLOOR",
                "ev_per_share": str(ev), "floor": str(floor)}
    return {"ok": True, "reason": None, "ev_per_share": str(ev)}
