"""CEILING_PINNED_PERSIST: one branch of the cancel ladder, and nothing else.

OWNER GO: "when a COMPLETE order is constrained by the pair ceiling and a
refreshed touch_moved condition would still recompute to the same legal maker
price, do not cancel solely because touch != px. Preserve the existing order
until another existing termination condition fires."

SHADOW ONLY. Nothing here submits, prices, cancels, reprices or deploys. It
reads orders the live engine already wrote and scores a counterfactual cancel
ladder over them. It is not imported by any production module and is in
neither the probe image nor the recorder's watch set.

WHAT THE ARM CHANGES -- stated as a diff against the live ladder, which is
`pmus_spring_l5_session._still_wanted.check()`:

    1  bbo_unreadable          unchanged
    2  book_one_sided          unchanged
    3  price_bound_left        unchanged (open/extend only; never COMPLETE)
    4  band_left               unchanged (open only; never COMPLETE)
    5  would_cross             unchanged -- and it still runs BEFORE 6
    6  touch_moved             <-- THE ONLY BRANCH TOUCHED
    7  l5_action_changed       unchanged

and branch 6 is suppressed only when ALL of these hold:

    * the relation is COMPLETE (open and extend are untouched);
    * the resting price was pinned by the pair ceiling, not chosen freely;
    * re-running the REAL pricing function against the refreshed book
      returns the SAME legal maker price.

If the recomputation returns a different legal price, or None, CONTROL
behaviour stands and the order is cancelled exactly as today.

WHAT THE ARM DOES NOT CHANGE. Price, pair ceiling, size, band, cap,
admission, concurrency, settlement horizon, post-only status, campaign
deadline, risk breakers and maker-only execution are all untouched, and every
one of them can still end the order. `would_cross` is evaluated before the
modified branch and is not conditional on the arm, so post-only can never be
breached by holding.

WHY THE PREDICATE IS DECIDABLE AND NOT A GUESS. Under the `l5_touch` regime
the ceiling-pinned price is

    px = floor((ceiling - avg_cost) / tick) * tick

which does not depend on `touch` at all once the cap binds, and does not
depend on `opp_ask` unless the post-only trim binds. So "the re-quote would
recompute to the same price" is arithmetic on values we already hold, not a
prediction. When `avg_cost` moves -- an EXTEND fills while the COMPLETE rests
-- the recomputed price moves with it and the arm falls back to CONTROL
automatically.

THE PRICING RULE IS MIRRORED, NOT REIMPLEMENTED. `ceiling_pinned_price`
reproduces the l5_touch branch of `pmus_spring_l5_session.spring_complement_price`
and a test calls the REAL function on a grid and asserts they agree, so a
drift in production shows up as a red test rather than as a quietly wrong
counterfactual.

NO FILL IS EVER INVENTED. A counterfactual order that outlives its historical
twin has NO observation attached to the extra time, because the historical
order was cancelled and the book path after the cancel was never recorded
against a live order. That time is `COUNTERFACTUAL_FILL_UNKNOWN` and is never
scored as a fill, a no-fill, or P&L.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Mapping, Optional, Sequence

CONTROL = "CONTROL"
ARM = "CEILING_PINNED_PERSIST"
ARMS = (CONTROL, ARM)

# Production values, mirrored as VALUES and tied to their owning module by a
# test. Leaving them out is what turns a counterfactual into a fantasy.
COMPLETION_PAIR_CEILING = D("1.00")     # pmus_spring_l5_session.completion_pair_ceiling()
RELATIONS_REQUOTED_BY_CONTROL = ("open", "extend", "complete")

OBSERVED_FILL = "OBSERVED_FILL"
OBSERVED_NO_FILL = "OBSERVED_NO_FILL"
COUNTERFACTUAL_FILL_UNKNOWN = "COUNTERFACTUAL_FILL_UNKNOWN"
FILL_CLASSES = (OBSERVED_FILL, OBSERVED_NO_FILL, COUNTERFACTUAL_FILL_UNKNOWN)

# every reason the live ladder can return, in its live order
LADDER = ("bbo_unreadable", "book_one_sided", "price_bound_left", "band_left",
          "would_cross", "touch_moved", "l5_action_changed")
# terminations outside the ladder, in _await_order
OUTER = ("filled", "filled_on_cancel", "filled_in_cancel_race", "deadline",
         "unconfirmed_cancel", "rejected")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def _floor(v: D, tick: D) -> D:
    return (v / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def ceiling_pinned_price(avg_cost: D, opp_ask: D, tick: D, touch: D,
                         ceiling: D = COMPLETION_PAIR_CEILING
                         ) -> tuple[Optional[D], dict]:
    """The l5_touch branch of `spring_complement_price`, mirrored.

    Returns (price, info). A None price means the production function would
    also refuse, and the arm must then defer to CONTROL.
    """
    px = _floor(touch, tick)
    econ_max = ceiling - avg_cost
    capped = px > econ_max
    if capped:
        px = _floor(econ_max, tick)
    trimmed = px >= opp_ask
    if trimmed:
        px = _floor(opp_ask - tick, tick)
    one = D("1")
    info = {"capped_by_pair_ceiling": bool(capped),
            "post_only_trim": bool(trimmed),
            "economic_max_px": str(econ_max),
            "pair_at_chosen": str(avg_cost + px)}
    if px < tick or px > one - tick:
        return None, {**info, "regime": "outside_venue_price_range"}
    if avg_cost + px > ceiling:
        return None, {**info, "regime": "above_completion_pair_ceiling"}
    return px, {**info, "regime": "l5_touch", "chosen_px": str(px)}


def arm_may_hold(resting_px: D, avg_cost: D, opp_ask: D, tick: D, touch: D,
                 relation: str, ceiling: D = COMPLETION_PAIR_CEILING) -> dict:
    """Would the arm suppress the touch_moved cancel for this order?

    Three conditions, all required. Anything else is CONTROL.
    """
    if relation != "complete":
        return {"hold": False, "why": "relation_not_complete"}
    px, info = ceiling_pinned_price(avg_cost, opp_ask, tick, touch, ceiling)
    if px is None:
        return {"hold": False, "why": f"recompute_refuses:{info.get('regime')}",
                **info}
    if not info["capped_by_pair_ceiling"]:
        return {"hold": False, "why": "price_not_pinned_by_the_ceiling", **info}
    if px != resting_px:
        return {"hold": False,
                "why": f"recompute_moves_price:{resting_px}->{px}", **info}
    return {"hold": True, "why": "ceiling_pinned_and_recompute_is_identical",
            **info}


def cancel_decision(arm: str, *, relation: str, resting_px: D, touch: D,
                    opp_ask: D, avg_cost: Optional[D], tick: D,
                    book_readable: bool = True, book_two_sided: bool = True,
                    entry_ok: bool = True, in_band: bool = True,
                    l5_relation_now: Optional[str] = None,
                    ceiling: D = COMPLETION_PAIR_CEILING) -> Optional[str]:
    """The live cancel ladder, in the live order, with exactly one branch
    made arm-dependent. None means keep resting.

    Every safety branch is evaluated for BOTH arms and is never skipped:
    an unreadable or one-sided book, and a price at or through the opposite
    ask, cancel immediately under the arm exactly as under CONTROL.
    """
    if not book_readable:
        return "bbo_unreadable"
    if not book_two_sided:
        return "book_one_sided"
    if relation in ("open", "extend") and not entry_ok:
        return f"price_bound_left:{touch}"
    if relation == "open" and not in_band:
        return f"band_left:{touch}"
    if resting_px >= opp_ask:                    # post-only, never suspended
        return f"would_cross:{opp_ask}"
    if relation in RELATIONS_REQUOTED_BY_CONTROL and touch != resting_px:
        if arm == ARM and avg_cost is not None and arm_may_hold(
                resting_px, avg_cost, opp_ask, tick, touch, relation,
                ceiling)["hold"]:
            pass                                 # THE ONE CHANGED BRANCH
        else:
            return f"touch_moved:{touch}"
    if l5_relation_now is not None and l5_relation_now != relation:
        return f"l5_action_changed:{l5_relation_now}"
    return None


def is_ceiling_pinned(order: Mapping[str, Any], tick: D,
                      avg_cost: Optional[D] = None,
                      ceiling: D = COMPLETION_PAIR_CEILING
                      ) -> tuple[Optional[bool], str]:
    """Was this resting price pinned by the ceiling rather than chosen?

    Two routes, and the weaker one is labelled as weaker:

    `recomputed_from_avg_cost` -- EXACT. The pinned price is
    `floor((ceiling - avg_cost)/tick)*tick` and does not involve the touch
    at all, so holding the held-side average cost decides it outright.

    `derived_from_post_time_touch` -- WEAKER, and only sound when the touch
    is the one recorded AT POST TIME. `px < floor(touch/tick)*tick` means
    something lowered the price below the touch; that is the ceiling in the
    l5_touch regime unless a post-only trim also bound.
    A touch read from a CANCEL reason is the touch that MOVED, not the touch
    we priced against, and using it would label every re-quoted order pinned.
    Callers must not pass one.

    Returns (verdict, provenance). None is "cannot tell", never False --
    a missing input is not evidence of a free price.
    """
    px = _d(order.get("px"))
    if px is None:
        return None, "no_price"
    if avg_cost is not None:
        return bool(px == _floor(ceiling - avg_cost, tick)), \
            "recomputed_from_avg_cost"
    touch = _d(order.get("l5_touch_px"))
    if touch is None:
        return None, "no_post_time_touch_and_no_avg_cost"
    return bool(px < _floor(touch, tick)), "derived_from_post_time_touch"


def chain_orders(orders: Sequence[Mapping[str, Any]], tick: D,
                 avg_cost_key: Optional[str] = None,
                 ceiling: D = COMPLETION_PAIR_CEILING
                 ) -> list[list[Mapping[str, Any]]]:
    """Group consecutive COMPLETE attempts the arm would have merged.

    A chain is a maximal run of attempts in one market that (a) share a
    posted price, (b) are ceiling-pinned, and (c) each ended in
    `repriced:touch_moved` -- except the last, which may end any way. Under
    the arm those k attempts are ONE order; under CONTROL they are k.

    This is a reconstruction of what the arm would have merged, not a claim
    about what the merged order would have done.
    """
    chains: list[list[Mapping[str, Any]]] = []
    cur: list[Mapping[str, Any]] = []
    for o in orders:
        ac = _d(o.get(avg_cost_key)) if avg_cost_key else None
        pinned, _prov = is_ceiling_pinned(o, tick, ac, ceiling)
        px = _d(o.get("px"))
        if cur:
            prev = cur[-1]
            same = (_d(prev.get("px")) == px and pinned is True
                    and str(prev.get("end_reason") or "").startswith(
                        "repriced:touch_moved"))
            if same:
                cur.append(o)
                continue
            chains.append(cur)
            cur = []
        if pinned is True:
            cur = [o]
    if cur:
        chains.append(cur)
    return [c for c in chains if c]


def classify_chain(chain: Sequence[Mapping[str, Any]]) -> dict:
    """What the tape can and cannot say about the merged order.

    OBSERVED_FILL             an attempt in the chain filled. The merged
                              order was live at that instant too, and with
                              no worse queue position, so the fill is
                              observed rather than imputed.
    OBSERVED_NO_FILL          every attempt rested and none filled. This
                              covers ONLY the union of the observed rest
                              windows.
    COUNTERFACTUAL_FILL_UNKNOWN
                              the gaps between attempts, and any time past
                              the last cancel, carry NO observation. The
                              historical order was cancelled, so nothing
                              was resting there and nothing can be claimed.
    """
    filled = [o for o in chain if (_d(o.get("filled_qty")) or D("0")) > 0]
    observed_rest = sum(float(_d(o.get("rest_s")) or 0) for o in chain)
    polls = sum(int((o.get("rest_book") or {}).get("polls") or 0)
                for o in chain)
    if filled:
        klass = OBSERVED_FILL
    elif len(chain) > 1:
        # the merged order outlives every individual attempt, so part of its
        # life is unobserved even though the attempts themselves were not
        klass = COUNTERFACTUAL_FILL_UNKNOWN
    else:
        klass = OBSERVED_NO_FILL
    return {
        "attempts_merged": len(chain),
        "reposts_eliminated": len(chain) - 1,
        "observed_rest_s_total": round(observed_rest, 3),
        "book_polls_total": polls,
        "class": klass,
        "observed_fill_qty": str(sum((_d(o.get("filled_qty")) or D("0"))
                                     for o in chain)),
        "unobserved": ("the gaps between cancel and repost, and all time past "
                       "the final cancel: nothing was resting there, so no "
                       "fill and no no-fill can be claimed"),
    }


def segment_chain_by_deadline(chain: Sequence[Mapping[str, Any]],
                              deadline_s: float) -> dict:
    """How many orders the merged chain REALLY becomes.

    The arm suppresses the touch_moved cancel. It does NOT change the rest
    bound, so a held order still dies at the model's own next re-evaluation
    and a fresh order is posted after it -- which is CONTROL behaviour, not
    something the arm removes.

    Merging a 23-attempt chain into ONE order would therefore overstate the
    arm by the number of deadline expiries inside it. This walks the chain's
    observed rest time and starts a new order every time the accumulated
    rest reaches the bound.

    Returns the honest count and the naive one, so the gap is visible rather
    than buried.
    """
    if deadline_s <= 0:
        return {"orders_under_arm": len(chain), "naive_orders_under_arm": 1,
                "basis": "no usable rest bound"}
    orders, acc, unmeasured = 1, 0.0, 0
    for o in chain:
        r = _d(o.get("rest_s"))
        if r is None:
            unmeasured += 1
            continue
        acc += float(r)
        while acc >= deadline_s:
            acc -= deadline_s
            orders += 1
    return {
        "orders_under_arm": orders,
        "naive_orders_under_arm": 1,
        "deadline_expiries_inside_the_chain": orders - 1,
        "attempts_under_control": len(chain),
        "reposts_eliminated": max(0, len(chain) - orders),
        "reposts_eliminated_naive": len(chain) - 1,
        "attempts_with_no_rest_time": unmeasured,
        "basis": (f"observed rest time segmented at a {deadline_s}s bound; "
                  "the arm does not change that bound"),
    }


def horizon_at_natural_boundaries(chain: Sequence[Mapping[str, Any]],
                                  deadline_s: float) -> dict:
    """Which EXISTING termination would have ended the merged order?

    Evaluated only from what the tape recorded. A boundary we cannot see is
    reported as UNKNOWN, never assumed not to have fired.
    """
    reasons = [str(o.get("end_reason") or "") for o in chain]
    last = reasons[-1] if reasons else ""
    observed_rest = sum(float(_d(o.get("rest_s")) or 0) for o in chain)
    # would_cross is decidable from the rest book: the closest the opposite
    # ask came against the resting price
    crossed = False
    for o in chain:
        moa = _d((o.get("rest_book") or {}).get("min_opp_ask"))
        px = _d(o.get("px"))
        if moa is not None and px is not None and px >= moa:
            crossed = True
    if any(r.startswith("filled") for r in reasons):
        boundary, basis = "filled", "observed on an attempt in the chain"
    elif crossed:
        boundary, basis = "would_cross", "rest_book min_opp_ask reached our price"
    elif last.startswith("rejected"):
        boundary, basis = "rejected", "observed on the final attempt"
    elif last.startswith("l5_action_changed"):
        boundary, basis = "l5_action_changed", "observed on the final attempt"
    elif last == "deadline":
        boundary, basis = "deadline", "observed on the final attempt"
    elif observed_rest >= deadline_s:
        boundary, basis = ("deadline",
                           "merged observed rest already reaches the rest bound")
    else:
        boundary, basis = ("UNKNOWN",
                           "the chain ended in a cancel the arm removes, and "
                           "no other boundary was observed to fire")
    return {"boundary": boundary, "basis": basis,
            "observed_rest_s": round(observed_rest, 3),
            "rest_bound_s": deadline_s,
            "reaches_rest_bound": observed_rest >= deadline_s}
