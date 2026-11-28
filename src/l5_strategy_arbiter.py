"""One choice a step, between the straddle and the late favourite.

WHY AN ARBITER AND NOT TWO CONTROLLERS. Both strategies want the same
dollars, the same market and sometimes the same SIDE of it. Two
controllers reading one budget would each see room the other had already
promised away; two controllers holding one inventory would each read a
position the other created as something to react to. So there is one
decision a step, taken here, and `l5_btc5m_controller.decide` calls it.

THE FOUR ACTIONS, as the owner specified them on 2026-11-16:

  KEEP_STRADDLE   leave the resting straddle orders alone.
  HANDOFF         cancel the ONE conflicting favourite-side rung and let
                  the late favourite have the side.
  COMPLETE_PAIR   finish an unmatched straddle leg.
  HOLD            hold what we have and add nothing.

T-30 IS NOT A REASON TO CANCEL ANYTHING. The clock only makes the late
favourite ELIGIBLE. Taking the side still has to beat keeping the rung,
and `continuation_usd` is what keeping it is worth.

WHAT CONTINUATION VALUE IS, AND WHAT IT IS NOT. Measured on 536 shadow
windows: 3 of 549 cheap-rung touches happened at t >= 270, worth +$0.76
in total, about +0.14c a window. That is THREE EVENTS. The point
estimate is close to meaningless and must not be read as a guaranteed
economic value -- the owner said so explicitly and this module agrees in
code: `continuation_usd` is an INPUT, it carries no default, and a
caller that does not supply one gets NO_CONTINUATION_ESTIMATE rather
than a free zero. What the measurement supports is only the weak claim
that the number is SMALL, which is what makes the handoff worth testing.

A HANDOFF TAKES TWO STEPS, and that is the safety property rather than
an inefficiency. This step emits only the cancel. The replacement is not
emitted until a later step sees the order gone from `resting` -- which
is the executor's venue-confirmed view, not a hope. If the cancel is
rejected, uncertain, or partially filled, the rung is still resting, the
late favourite still reports SIDE_ALREADY_WORKED, and nothing is placed.
There is no code path that submits the replacement while the
cancellation is unresolved, because the two never happen in one step.

NEVER THE OPPOSITE SIDE. A resting order on the side the late favourite
does NOT want is a potential pair completion, which is the best
economics this system has (+84c a share). It is never cancelled here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

D = Decimal
ZERO = D("0")

KEEP_STRADDLE = "KEEP_STRADDLE"
HANDOFF = "HANDOFF"
COMPLETE_PAIR = "COMPLETE_PAIR"
HOLD = "HOLD"
ACTIONS = (KEEP_STRADDLE, HANDOFF, COMPLETE_PAIR, HOLD)


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


def choose(*, straddle_orders: Any, completion_order: Any,
           completion_ev_usd: Any, late: Any,
           continuation_usd: Any, handoff_margin_usd: Any) -> Dict[str, Any]:
    """One action, the orders and cancels it implies, and why.

    `late` is a `l5_late_favorite.evaluate` result. `straddle_orders` and
    `completion_order` are what the straddle would do on its own, so
    choosing another action leaves them unemitted rather than undone.
    """
    out: Dict[str, Any] = {"action": None, "why": None, "orders": [],
                           "cancels": [], "evaluation": {}}

    def pick(action: str, why: str, *, orders: Optional[List] = None,
             cancels: Optional[List] = None, **ev: Any) -> Dict[str, Any]:
        out.update(action=action, why=why, orders=list(orders or []),
                   cancels=list(cancels or []))
        out["evaluation"].update(ev)
        return out

    s_orders = list(straddle_orders or [])
    lat = late if isinstance(late, Mapping) else {}
    #: the candidate, whether it was placeable or only blocked by the
    #: conflict this arbiter exists to resolve.
    cand = lat.get("order") or lat.get("blocked_order")
    cand_ev = _dec(lat.get("ev_usd"))
    conflicts = list(lat.get("conflicts") or [])
    out["evaluation"].update(
        late_reason=lat.get("reason"),
        late_ev_usd=None if cand_ev is None else str(cand_ev),
        late_eligible=bool(cand))

    #: 1. A PAIR BEATS EVERYTHING IT CAN BE COMPARED WITH. Completing an
    #:    unmatched leg is the only action here with measured positive
    #:    economics, so it is taken whenever it is due and positive.
    c_ev = _dec(completion_ev_usd)
    if completion_order and c_ev is not None and c_ev > ZERO:
        return pick(COMPLETE_PAIR, "COMPLETION_DUE",
                    orders=[completion_order], completion_ev_usd=str(c_ev))

    #: 2. NO CANDIDATE: the straddle's own plan stands.
    if not cand:
        if s_orders:
            return pick(KEEP_STRADDLE, "STRADDLE_ONLY", orders=s_orders)
        return pick(HOLD, str(lat.get("reason") or "NOTHING_TO_DO"))

    #: 3. THE CANDIDATE IS PLACEABLE -- no side conflict. It is additive,
    #:    so the straddle's orders go out WITH it. One budget already
    #:    bounded both: the late evaluator was given the room left after
    #:    the straddle allocated.
    if lat.get("ok") is True:
        return pick(KEEP_STRADDLE, "STRADDLE_AND_LATE_FAVORITE",
                    orders=s_orders + [cand])

    #: 4. BLOCKED BY A CONFLICT: this is the only case a cancel is even
    #:    considered, and it is still a comparison.
    if lat.get("reason") != "SIDE_ALREADY_WORKED":
        if s_orders:
            return pick(KEEP_STRADDLE, "STRADDLE_ONLY", orders=s_orders)
        return pick(HOLD, str(lat.get("reason")))

    cont = _dec(continuation_usd)
    margin = _dec(handoff_margin_usd)
    if cont is None:
        #: NO FREE ZERO. An unsupplied continuation value would make
        #: every handoff look free.
        return pick(KEEP_STRADDLE, "NO_CONTINUATION_ESTIMATE",
                    orders=s_orders)
    if margin is None or margin < ZERO:
        return pick(KEEP_STRADDLE, "NO_HANDOFF_MARGIN", orders=s_orders)
    if cand_ev is None:
        return pick(KEEP_STRADDLE, "LATE_EV_UNREADABLE", orders=s_orders)
    out["evaluation"].update(continuation_usd=str(cont),
                             handoff_margin_usd=str(margin),
                             threshold_usd=str(cont + margin))
    if cand_ev <= cont + margin:
        #: T-30 ALONE CHANGES NOTHING.
        return pick(KEEP_STRADDLE, "LATE_EV_BELOW_CONTINUATION",
                    orders=s_orders)

    #: EXACTLY THE CONFLICTING ORDERS, BY ID, AND NOTHING ELSE. A cancel
    #: without an id is not an exact cancel, so it is refused rather than
    #: widened into "everything on that side".
    #: STRIPPED, because a whitespace-only id is truthy and would emit a
    #: cancel carrying a blank id -- which the venue would either reject
    #: or, worse, interpret.
    ids = [str(c.get("id") or "").strip() for c in conflicts]
    if not ids or any(not i for i in ids):
        return pick(KEEP_STRADDLE, "CONFLICT_NOT_IDENTIFIED",
                    orders=s_orders, conflicts=conflicts)
    #: THE REPLACEMENT IS NOT EMITTED HERE. A later step places it, once
    #: `resting` no longer shows these ids.
    picked = pick(HANDOFF, "LATE_FAVORITE_BEATS_CONTINUATION",
                  cancels=[{"op": "CANCEL", "id": i} for i in ids])
    #: TOP-LEVEL, not inside `evaluation`: it is what the NEXT step will
    #: place, so the executor reads it as data rather than as commentary.
    #: It is deliberately NOT in `orders` -- nothing here may submit it.
    picked["deferred_order"] = cand
    return picked
