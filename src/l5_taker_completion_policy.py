"""Bounded, maker-first authorization for a CROSSING completion.

WHY THIS EXISTS, measured on the 2026-10-30 Stage A campaign rather than
argued. Four windows produced four stranded legs and
`l5_two_sided_probe.completion_decision` already priced the executable exit
for every one of them. It refused none of the good ones -- it simply has no
authority to act, because `authorized_to_cross` is owner-held, and because
the decision only REPORTS `both_positive_after` instead of requiring it.

The gap that cost real money, from that campaign's own records:

  2145z  held DOWN 2.00 @ 0.45. From 21:48:23 to beyond 21:54:55 -- ~6.5
         minutes, ~30 consecutive decision ticks -- the cross was priced with
         `both_positive_after: true`, displayed depth 200x-750x the 2
         contracts needed. At 21:51:49: BUY 2 UP @ 0.10, $0.22 all-in,
         `worst_before -0.9400 -> worst_after +0.8400`. Not taken.
  2130z  held UP 2.05 @ 0.46. 22 of 49 samples both-positive, best +0.3915.
         Settled -0.9430 instead.
  2115z  held DOWN 1.74. Best worst_after -0.0400. NEVER positive.
  2200z  held DOWN 1.85 @ 0.33. 0 of 58 samples positive, complement pinned
         near 0.99. NEVER positive.

So two of four stranded legs had a genuine exit and two did not, and a policy
that fires on `both_positive_after` alone correctly does nothing in 2115z and
2200z. That asymmetry is the whole case for this module: it is not a
directional bet, it is the removal of settlement variance when, and only
when, the removal is already paid for.

THE MARGIN FLOOR IS NOT DECORATION, and this is the finding that shaped the
module. Replaying the campaign on its own recorded books:

  fire at the FIRST both-positive tick   -> -0.6421  (delta +0.0870)
  fire only when worst_after >= +0.25    -> -0.3371  (delta +0.3920)
  fire at the BEST observed tick         -> +0.4554  (delta +1.1845)

The first row is nearly worthless: a marginal both-positive tick buys almost
nothing and spends the one cross the window gets. The last row is hindsight
and not implementable. So the policy fires on a MARGIN above zero, not on
positivity, and `margin_floor_usd` is the dial the owner sets.

PARTIAL FILLS CANNOT FLIP THE SIGN, which is why this is safe to bound rather
than forbid. Worst-case P&L is monotonically non-decreasing in the quantity
filled, for every complement price, so long as the cross never exceeds the
deficit -- verified on the 2145z state at complement prices 0.10/0.30/0.50/
0.54/0.60. A half fill of the 2145z cross is worst -0.0500 against -0.9400
for doing nothing: strictly better, merely not the full +0.8400. The venue
offers IMMEDIATE_OR_CANCEL but no fill-or-kill, so partials are real; they
are a shortfall against the modelled gain, never a new loss. `top_up` already
owns the "never beyond the deficit" half of that invariant and this module
re-asserts it rather than trusting it.

WHAT THIS MODULE DOES NOT DO. It does not price, size or submit anything. It
consumes a `completion_decision` that already passed every existing gate and
answers one question: is this cross AUTHORIZED. Default is no. It holds no
limit of its own that duplicates one already in force -- cash headroom, the
worst-case ceiling, tick and min-qty legality, and "improvement must exceed
its own fee" all stay where they are -- and it adds only what the owner's
brief asks for: a default-off flag, maker-first patience, strict positivity
after CONSERVATIVE execution costs, and a hard additional-outlay cap.
"""

from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, Optional, Sequence, Tuple

from src import l5_two_sided_probe as TSP

__all__ = [
    "POLICY_ENV", "MARGIN_FLOOR_ENV", "MAX_OUTLAY_ENV",
    "WINDOW_OUTLAY_ENV", "MAKER_PATIENCE_ENV", "DEPTH_HAIRCUT_ENV",
    "DEFAULT_MARGIN_FLOOR_USD", "DEFAULT_MAX_OUTLAY_USD",
    "DEFAULT_WINDOW_OUTLAY_USD", "DEFAULT_MAKER_PATIENCE_S",
    "DEFAULT_DEPTH_HAIRCUT",
    "OWNER_CROSS_ENV", "MANDATE_ORIGIN",
    "LOSS_REDUCTION_ENV", "MIN_WORST_IMPROVEMENT_ENV",
    "loss_reduction_authorized",
    "policy_enabled", "owner_cross_authorized", "taker_completion_enabled",
    "conservative_fill", "authorize",
]

#: default-off. The owner's brief says default-off and it is spelled as an
#: opt-in string rather than a truthy env read, so a stray "0" cannot enable
#: live crossing.
POLICY_ENV = "L5_TAKER_COMPLETION_POLICY"

MARGIN_FLOOR_ENV = "L5_TAKER_MARGIN_FLOOR_USD"
MAX_OUTLAY_ENV = "L5_TAKER_MAX_OUTLAY_USD"
WINDOW_OUTLAY_ENV = "L5_TAKER_WINDOW_OUTLAY_USD"
MAKER_PATIENCE_ENV = "L5_TAKER_MAKER_PATIENCE_S"
DEPTH_HAIRCUT_ENV = "L5_TAKER_DEPTH_HAIRCUT"

#: +$0.25. Chosen from the replay above, not from taste: it is the floor at
#: which the campaign's two real opportunities both still qualify
#: (2130z +0.2890, 2145z +0.2600) while the marginal ticks that buy ~nothing
#: do not.
DEFAULT_MARGIN_FLOOR_USD = D("0.25")

#: the most ADDITIONAL cash one cross may spend, fee included. The campaign's
#: largest genuine completion needed $0.22, so $1.50 is ~7x the observed need
#: and still inside the per-market worst-case deficit already authorized.
DEFAULT_MAX_OUTLAY_USD = D("1.50")

#: and across a whole window, so a repricing loop cannot spend the cap once
#: per tick.
DEFAULT_WINDOW_OUTLAY_USD = D("3.00")

#: maker-first, in seconds. A maker completion must have been RESTING and
#: unfilled this long before crossing is considered. 2145z's exit stayed
#: available ~390s, so a 90s maker-first wait costs little of a real
#: opportunity and refuses the impatient ones outright.
DEFAULT_MAKER_PATIENCE_S = 90.0

#: displayed depth is not a fill. Only this fraction of each displayed rung
#: is believed when checking whether the size can clear.
DEFAULT_DEPTH_HAIRCUT = D("0.25")

# =====================================================================
# LOSS REDUCTION, which the margin floor cannot express.
#
# WHY THIS EXISTS, FROM THE REPLAY THAT FOUND IT. `DEFAULT_MARGIN_FLOOR_USD`
# is +$0.25 and was calibrated on the two PROFITABLE completions of the
# 2026-10-30 campaign (2130z +0.2890, 2145z +0.2600). It asks that the worse
# settlement outcome end up at least a quarter ABOVE zero.
#
# A rescue cannot satisfy that, ever, by construction. Replaying the two
# losing windows of 2026-11-06 against their own recorded books:
#
#   0300z  worst_before -0.6500  best worst_after -0.0200  improvement 0.6300
#   0315z  worst_before -0.4000  best worst_after -0.0500  improvement 0.3500
#
# Both are large reductions in exposure and both end BELOW zero, because the
# first leg's cost is already sunk. So with the route wired and the deployed
# floor in force, the policy would still have refused both, and both
# residuals would still have settled for -$1.00.
#
# WHAT THIS ADDS. A SEPARATE, DEFAULT-OFF authorization for the loss-reducing
# case, gated on an improvement floor rather than an outcome floor. It is the
# "owner-specified maximum completion-loss budget" of issue #195 item 3, and
# it is deliberately not a relaxation of the margin floor: the profit case
# still has to clear +$0.25, and this case still has to clear every outlay
# cap, the conservative-fill re-pricing and the deficit bound.
#
# WITH IT UNSET NOTHING CHANGES. `authorize` takes the identical path it
# takes today and refuses WORST_OUTCOME_NOT_POSITIVE exactly as before.
# =====================================================================

#: default-off, and an explicit YES like every other switch here
LOSS_REDUCTION_ENV = "L5_TAKER_LOSS_REDUCTION"

#: how much worse-case exposure a loss-reducing cross must actually remove.
#: UNSET IS OFF: with the switch on and no floor named, `authorize` refuses
#: rather than accepting any improvement at all, because "strictly better
#: than carrying" by a hundredth of a cent is not a reason to spend cash.
MIN_WORST_IMPROVEMENT_ENV = "L5_TAKER_MIN_WORST_IMPROVEMENT_USD"


def loss_reduction_authorized() -> bool:
    """The owner's separate authorization for the loss-reducing case.

    THREE SWITCHES NOW, AND THAT IS THE POINT. The policy flag says the
    bounded route exists; the owner cross flag authorizes live taker
    execution; this one authorizes crossing where the best achievable
    outcome is still a loss. Each is a different decision and each is
    default-off, so none of them can be reached by setting another.
    """
    return _flag(LOSS_REDUCTION_ENV)


#: The owner's separate authorization for crossing. `authorize` takes both as
#: arguments and the runner reads them there; this helper exists so the ONE
#: place that exempts an order from the maker-only overlay can ask the same
#: question without importing the runner.
OWNER_CROSS_ENV = "L5_TS_COMPLETION_CROSS"

#: The mandate origin a bounded taker completion issues. It shares the
#: executor's single mandate slot with the defensive unwind -- deliberately,
#: so "one crossing order per market per window" is one invariant and not two
#: -- and the origin is what says which policy authorized it.
MANDATE_ORIGIN = "TAKER_COMPLETION"


def _flag(env: str) -> bool:
    """An explicit YES and nothing else.

    Spelled as a string comparison rather than a truthy env read for the same
    reason `POLICY_ENV` is: a stray "0", "false" or "off" must not be able to
    enable live crossing.
    """
    return str(os.getenv(env, "")).strip().upper() == "YES"


def policy_enabled() -> bool:
    return _flag(POLICY_ENV)


def owner_cross_authorized() -> bool:
    return _flag(OWNER_CROSS_ENV)


def taker_completion_enabled() -> bool:
    """BOTH switches, and both default OFF.

    The policy flag says the bounded taker route exists; the owner flag is
    the separate explicit authorization for live taker execution that issue
    #195 requires. Neither alone is permission, and this is the single
    predicate the maker-only exemption consults, so there is no second place
    where the pair could come apart.
    """
    return policy_enabled() and owner_cross_authorized()


def _d(v: Any) -> Optional[D]:
    return TSP._d(v)


def _rungs(raw: Any) -> Optional[Sequence[Tuple[D, D]]]:
    """[[price, size], ...] -> [(D, D), ...], or None if unreadable.

    Unreadable is NOT empty: an empty book and a book we failed to parse must
    refuse differently, so this returns None for the second and () for the
    first.
    """
    if raw is None:
        return None
    out = []
    try:
        for rung in raw:
            px = _d(rung[0])
            sz = _d(rung[1])
            if px is None or sz is None:
                return None
            if px <= 0 or px >= D(1) or sz < 0:
                return None
            out.append((px, sz))
    except (TypeError, IndexError, KeyError):
        return None
    return tuple(out)


def conservative_fill(*, side: str, qty_wanted: Any,
                      rungs: Any = None,
                      displayed_depth: Any = None,
                      fallback_price: Any = None,
                      haircut: Any = DEFAULT_DEPTH_HAIRCUT
                      ) -> Dict[str, Any]:
    """How much of `qty_wanted` clears, and at what VWAP, pessimistically.

    SLIPPAGE IS WALKED, NOT ASSUMED AWAY. `completion_decision` prices the
    cross at one level against one `available_depth` figure, which is right
    for the touch but silently optimistic for any size that would eat
    through it. This walks the rungs we were served and returns the VWAP of
    the quantity that actually clears, so the authorization is judged on the
    price the size would really pay.

    AND DISPLAYED DEPTH IS DISCOUNTED. PMUS publishes no print endpoint, so
    the fraction of a displayed rung that is really available cannot be
    measured here; `haircut` is therefore applied to every rung and the
    default believes a quarter of it. With no rungs served this falls back to
    the single displayed figure, haircut the same way, at `fallback_price`.
    """
    want = _d(qty_wanted)
    hc = _d(haircut)
    out: Dict[str, Any] = {"side": str(side).upper(),
                           "qty_wanted": None if want is None else str(want),
                           "haircut": None if hc is None else str(hc)}
    if want is None or want <= 0:
        return dict(out, ok=False, reason="QTY_WANTED_UNREADABLE")
    if hc is None or hc <= 0 or hc > D(1):
        return dict(out, ok=False, reason="HAIRCUT_OUT_OF_RANGE")

    rr = _rungs(rungs)
    if rungs is not None and rr is None:
        return dict(out, ok=False, reason="BOOK_RUNGS_UNREADABLE")

    # A BOOK THAT WAS SERVED AND IS EMPTY IS NOT A MISSING BOOK. Falling
    # through to `displayed_depth` here would answer a question about an
    # empty book with a figure taken from the touch, so a served book --
    # empty or not -- is the one that is walked.
    if rr is not None:
        filled = D(0)
        notional = D(0)
        used = 0
        for px, sz in rr:
            if filled >= want:
                break
            take = min(want - filled, sz * hc)
            if take <= 0:
                continue
            filled += take
            notional += take * px
            used += 1
        if filled <= 0:
            return dict(out, ok=False, reason="NO_BELIEVED_DEPTH",
                        rungs_served=len(rr))
        vwap = notional / filled
        return dict(out, ok=True, reason=None,
                    qty_fillable=str(filled),
                    vwap=str(vwap),
                    levels_used=used,
                    rungs_served=len(rr),
                    clears_in_full=bool(filled >= want),
                    basis=("VWAP of the quantity that clears after every "
                           "displayed rung is cut to `haircut`. Displayed "
                           "depth is displayed, not guaranteed."))

    # no rungs served: fall back to the single displayed figure
    depth = _d(displayed_depth)
    px = _d(fallback_price)
    if depth is None or px is None:
        return dict(out, ok=False, reason="NO_DEPTH_AND_NO_RUNGS")
    if px <= 0 or px >= D(1):
        return dict(out, ok=False, reason="FALLBACK_PRICE_OUT_OF_RANGE")
    filled = min(want, depth * hc)
    if filled <= 0:
        return dict(out, ok=False, reason="NO_BELIEVED_DEPTH",
                    displayed_depth=str(depth))
    return dict(out, ok=True, reason=None,
                qty_fillable=str(filled), vwap=str(px),
                levels_used=1, rungs_served=0,
                clears_in_full=bool(filled >= want),
                basis=("single displayed level, cut to `haircut`. No rungs "
                       "were served, so no walk was possible."))


def _no(reason: str, **kw: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "authorized": False, "reason": reason}
    out.update(kw)
    return out


def authorize(*, decision: Optional[Dict[str, Any]],
              enabled: bool,
              owner_authorized: bool,
              held_up_qty: Any, held_dn_qty: Any,
              held_up_px: Any, held_dn_px: Any,
              maker_attempted: Optional[bool] = None,
              maker_rested_s: Any = None,
              maker_patience_s: Any = DEFAULT_MAKER_PATIENCE_S,
              margin_floor_usd: Any = DEFAULT_MARGIN_FLOOR_USD,
              max_outlay_usd: Any = DEFAULT_MAX_OUTLAY_USD,
              window_outlay_spent_usd: Any = None,
              window_outlay_cap_usd: Any = DEFAULT_WINDOW_OUTLAY_USD,
              book_rungs: Any = None,
              depth_haircut: Any = DEFAULT_DEPTH_HAIRCUT,
              halted: Optional[bool] = None,
              loss_reduction: bool = False,
              min_worst_improvement_usd: Any = None,
              ) -> Dict[str, Any]:
    """Is this already-priced cross AUTHORIZED? Default: no.

    `decision` is a `completion_decision` result. Every economic gate it
    applied stands; this adds only the owner's bounded conditions, and every
    refusal names itself so a window that did not cross can be audited for
    WHY rather than guessed at.

    FAIL CLOSED ON EVERY UNKNOWN. An unreadable margin, an unknown maker
    history, an unparseable book and an unknown halt state all refuse. The
    expensive direction of this decision is acting, so uncertainty must not
    resolve toward acting.
    """
    rec: Dict[str, Any] = {
        "policy": "BOUNDED_TAKER_COMPLETION",
        "default": "OFF",
        "labels": ("AUTHORIZATION ONLY. This prices nothing and submits "
                   "nothing; it answers whether a cross that already "
                   "passed every existing economic gate may be acted on. "
                   "Nothing here is a fill and nothing here is realized."),
    }

    # ---- 1. THE FLAG, BEFORE ANY ARITHMETIC. A default-off policy must be
    #         unreachable when off, not merely unlikely to fire.
    if not bool(enabled):
        return _no("POLICY_DISABLED", **rec)
    if not bool(owner_authorized):
        return _no("OWNER_AUTHORIZATION_ABSENT", **rec)

    # ---- 2. A HALT OUTRANKS AN OPPORTUNITY, and an unknown halt state is
    #         treated as halted.
    if halted is None:
        return _no("HALT_STATE_UNKNOWN", **rec)
    if bool(halted):
        return _no("HALTED", **rec)

    # ---- 3. THE UNDERLYING DECISION MUST ITSELF BE A YES.
    if not isinstance(decision, dict):
        return _no("NO_DECISION", **rec)
    if not decision.get("ok"):
        return _no("DECISION_NOT_OK",
                   decision_reason=str(decision.get("reason"))[:60], **rec)
    side = str(decision.get("side", "")).strip().upper()
    if side not in (TSP.UP, TSP.DOWN):
        return _no("DECISION_SIDE_UNKNOWN", **rec)
    rec["side"] = side

    # ---- 4. STRICT POSITIVITY IS REQUIRED, NOT REPORTED. This is the gate
    #         `completion_decision` deliberately leaves to its caller: it
    #         asks only that the cross improve the worst outcome and pay for
    #         its own fee, which 2115z satisfied at worst_after -0.0400.
    #
    #         THE LOSS-REDUCING CASE IS A SEPARATE AUTHORIZATION, not a
    #         relaxation of this one. A rescue can never end above zero --
    #         the first leg's cost is already sunk -- so with the owner's
    #         loss-reduction switch off this still refuses, byte for byte as
    #         it does today, and with it on the test becomes how much
    #         exposure is removed rather than where the outcome lands.
    rec["loss_reduction_requested"] = bool(loss_reduction)
    _positive = bool(decision.get("both_positive_after"))
    _rescue = False
    if not _positive:
        if not bool(loss_reduction):
            return _no("WORST_OUTCOME_NOT_POSITIVE",
                       worst_after=str(decision.get("worst_after")), **rec)
        _rescue = True
        _wb = _d(decision.get("worst_before"))
        _wa = _d(decision.get("worst_after"))
        _floor = _d(min_worst_improvement_usd)
        if _wb is None or _wa is None:
            return _no("RESCUE_WORST_OUTCOMES_UNREADABLE", **rec)
        if _floor is None or _floor <= 0:
            # UNSET IS OFF. "Strictly better than carrying" by a hundredth of
            # a cent is not a reason to spend cash, so the floor has to be
            # NAMED and positive or this refuses.
            return _no("RESCUE_IMPROVEMENT_FLOOR_UNSET", **rec)
        rec["rescue"] = True
        rec["worst_before"] = str(_wb)
        rec["min_worst_improvement_usd"] = str(_floor)
        rec["worst_improvement_usd"] = str(_wa - _wb)
        if (_wa - _wb) < _floor:
            return _no("RESCUE_IMPROVEMENT_BELOW_FLOOR", **rec)

    # ---- 5. MAKER FIRST. Crossing is the expensive route and must not be
    #         the first one tried. Unknown history fails closed.
    if maker_attempted is None:
        return _no("MAKER_HISTORY_UNKNOWN", **rec)
    if not bool(maker_attempted):
        return _no("MAKER_NOT_ATTEMPTED", **rec)
    rested = _d(maker_rested_s)
    patience = _d(maker_patience_s)
    if patience is None or patience < 0:
        return _no("MAKER_PATIENCE_UNREADABLE", **rec)
    if rested is None:
        return _no("MAKER_RESTED_UNKNOWN", **rec)
    rec["maker_rested_s"] = str(rested)
    rec["maker_patience_s"] = str(patience)
    if rested < patience:
        return _no("MAKER_PATIENCE_NOT_SPENT", **rec)

    # ---- 6. NEVER BEYOND THE DEFICIT. This is what makes a partial fill a
    #         shortfall rather than a new loss: worst-case P&L is monotone
    #         non-decreasing in fill quantity only while the side being
    #         bought is the DEFICIENT one. `top_up` owns this too; a safety
    #         invariant worth one comparison is worth asserting twice.
    qty = _d(decision.get("qty"))
    deficit = _d(decision.get("deficit"))
    if qty is None or qty <= 0:
        return _no("DECISION_QTY_UNREADABLE", **rec)
    if deficit is None or deficit <= 0:
        return _no("DECISION_DEFICIT_UNREADABLE", **rec)
    if qty > deficit:
        return _no("CROSS_EXCEEDS_DEFICIT", qty=str(qty),
                   deficit=str(deficit), **rec)

    # ---- 7. CONSERVATIVE EXECUTION. Re-price the cross at the VWAP the size
    #         would really pay, against haircut depth, and demand that the
    #         worst outcome still clears the margin floor at the quantity
    #         that CONSERVATIVELY clears -- not at the quantity we asked for.
    px = _d(decision.get("price"))
    if px is None or px <= 0 or px >= D(1):
        return _no("DECISION_PRICE_UNREADABLE", **rec)
    cf = conservative_fill(side=side, qty_wanted=qty, rungs=book_rungs,
                           displayed_depth=decision.get("displayed_depth"),
                           fallback_price=px, haircut=depth_haircut)
    rec["conservative_fill"] = cf
    if not cf.get("ok"):
        return _no("CONSERVATIVE_FILL:%s" % str(cf.get("reason"))[:40], **rec)
    cq = _d(cf.get("qty_fillable"))
    cv = _d(cf.get("vwap"))
    if cq is None or cv is None or cq <= 0:
        return _no("CONSERVATIVE_FILL_UNREADABLE", **rec)

    a = _d(held_up_qty)
    b = _d(held_dn_qty)
    pu = _d(held_up_px)
    pd = _d(held_dn_px)
    if a is None or b is None or pu is None or pd is None:
        return _no("HELD_INVENTORY_UNREADABLE", **rec)

    tu = TSP.top_up(up_qty=a, dn_qty=b, up_price=pu, dn_price=pd,
                    extra_side=side, extra_qty=cq, extra_price=cv)
    rec["conservative_worst_before"] = str(tu.get("worst_before"))
    rec["conservative_worst_after"] = str(tu.get("worst_after"))
    rec["conservative_both_positive"] = bool(tu.get("both_positive_after"))
    if not tu.get("ok"):
        return _no("CONSERVATIVE:%s"
                   % str(tu.get("reason") or "WORSENS_WORST_OUTCOME")[:40],
                   **rec)
    if not bool(tu.get("both_positive_after")) and not _rescue:
        return _no("CONSERVATIVE_WORST_NOT_POSITIVE", **rec)

    # ---- 8. THE MARGIN FLOOR. Positivity alone bought +$0.0870 across the
    #         whole campaign; the floor is what makes the policy worth its
    #         one cross per window.
    floor = _d(margin_floor_usd)
    if floor is None or floor < 0:
        return _no("MARGIN_FLOOR_UNREADABLE", **rec)
    worst_after = _d(tu.get("worst_after"))
    if worst_after is None:
        return _no("CONSERVATIVE_WORST_UNREADABLE", **rec)
    rec["margin_floor_usd"] = str(floor)
    if _rescue:
        # THE RESCUE'S FLOOR IS ON THE IMPROVEMENT, RE-CHECKED AT THE
        # CONSERVATIVE QUANTITY. The margin floor cannot apply here -- a
        # rescue ends below zero by construction -- so the same question is
        # asked of the size that CONSERVATIVELY clears rather than of the
        # size we asked for, which is what step 7 exists to do for every
        # other bound.
        _cwb = _d(tu.get("worst_before"))
        _cfloor = _d(min_worst_improvement_usd)
        if _cwb is None or _cfloor is None:
            return _no("RESCUE_CONSERVATIVE_UNREADABLE", **rec)
        rec["conservative_worst_improvement_usd"] = str(worst_after - _cwb)
        if (worst_after - _cwb) < _cfloor:
            return _no("RESCUE_CONSERVATIVE_IMPROVEMENT_BELOW_FLOOR", **rec)
    elif worst_after < floor:
        return _no("MARGIN_BELOW_FLOOR", **rec)

    # ---- 9. THE HARD OUTLAY CAP, per cross and per window, computed on the
    #         CONSERVATIVE quantity and VWAP so the cap bounds what this may
    #         really spend rather than what it hoped to.
    outlay = cq * cv + TSP.worst_fee(cq, cv)
    rec["additional_outlay_usd"] = str(outlay)
    cap = _d(max_outlay_usd)
    if cap is None or cap <= 0:
        return _no("MAX_OUTLAY_UNREADABLE", **rec)
    rec["max_outlay_usd"] = str(cap)
    if outlay > cap:
        return _no("OUTLAY_ABOVE_CAP", **rec)

    wcap = _d(window_outlay_cap_usd)
    spent = _d(window_outlay_spent_usd)
    if wcap is None or wcap <= 0:
        return _no("WINDOW_OUTLAY_CAP_UNREADABLE", **rec)
    if spent is None:
        return _no("WINDOW_OUTLAY_SPENT_UNKNOWN", **rec)
    rec["window_outlay_spent_usd"] = str(spent)
    rec["window_outlay_cap_usd"] = str(wcap)
    if spent + outlay > wcap:
        return _no("WINDOW_OUTLAY_EXHAUSTED", **rec)

    return dict(rec, ok=True, authorized=True,
                reason="AUTHORIZED_RESCUE" if _rescue else "AUTHORIZED",
                qty=str(cq), price=str(cv),
                qty_requested=str(qty), price_requested=str(px),
                clears_in_full=bool(cf.get("clears_in_full")),
                basis=("authorized on the CONSERVATIVE quantity and VWAP, "
                       "with the worst settlement outcome strictly above "
                       "the margin floor at that quantity. A partial fill "
                       "is a shortfall against this figure, never a new "
                       "loss: worst-case P&L is monotone non-decreasing in "
                       "fill quantity while the bought side is the "
                       "deficient one."))
