"""What to do with a confirmed unmatched leg. Priced, ranked, and recorded.

THE DEFECT THIS CLOSES. 0100z filled DOWN 1 at own 0.4900 at 01:00:39 and
settled unmatched at -$0.4900. In the 14 minutes after the last cancel the
engine emitted 152 decision records and not one submission attempt: 69
`DECIDE_COOLDOWN`, then 72 `PAST_COMPLETE_CUTOFF` and 11 `PAST_TERMINAL`,
every one of the last 83 reading `NOT_AN_OPPORTUNITY` with verdict `N/A`.
The residual settled without a single record naming the position, its worst
case, and the fact that nothing was available. The engine stopped ASKING
rather than deciding.

WHAT THIS MODULE IS. A pure function that, given a confirmed fill and the
current book, enumerates every disposition of the residual, prices each one,
ranks them by a stated rule, and returns a record that says which was
selected and why each other was rejected. It is the decision; it is not the
order.

WHAT IT CANNOT DO, structurally. It holds no transport, no credentials and
no executor. It returns data. Nothing here can place, amend, cancel or
cross anything, and `selected` is only ever an action whose
`executable_now` is true -- so a disabled taker route can be PRICED and
REPORTED but can never be chosen. That is what makes the counterfactual
safe to compute.

THE EV IDENTITY, because it decides the ranking rule. If the complement's
own executable price is p, the market's own implied probability that our
held side loses is p, so

    EV(hold)     = (1-p)*(h - c) + p*(-c) = h*(1-p) - c
    EV(complete) = h - c - h*p            = h*(1-p) - c

They are EQUAL, exactly, and `test_declining_a_zero_edge_completion_is_ev_
neutral` already pins that. So expectancy cannot rank these actions and a
module that ranked on EV alone would be choosing by rounding error. What
differs is the WORST CASE: holding risks -c, while completing locks
h*(1-p) - c - fee with certainty. The measured consequence over 15 recorded
windows is that carrying realizes -$0.19 to -$0.25 a leg while completing
at the touch costs $0.03 to $0.06 -- the market keeps moving after it fills
a maker quote.

    RANKING RULE, stated once: among EXECUTABLE actions, maximise the worst
    case; break ties on EV at market; break remaining ties on the smaller
    queue ahead. Never on an unmeasured fill probability -- we do not have
    one, and `l5_queue_depletion` exists to say so precisely.

IT DEFERS, IT DOES NOT DUPLICATE. The pair ceilings come from
`max_pair_cost()` and `completion_pair_ceiling()`; whether a resting
completion may be repriced is answered by `completion_requote` itself, so
the PR #200 ceiling and the PR #202 recovery test cannot be contradicted
here by a second opinion. PRESERVE is ranked ABOVE hold by construction,
which is the same conclusion both of those PRs reached independently: a
resting quote inside the ceiling has the worst case of holding and strictly
better upside, so cancelling it is never an improvement.

NO FILL IS CLAIMED BY ANY FIGURE HERE. A maker action's upside is
conditional on a fill this module does not predict; only the taker
counterfactual is certain, and it is never selected.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
import os
from typing import Any, Dict, List, Mapping, Optional

from src import l5_two_sided_probe as TS

PRESERVE = "PRESERVE"
REPRICE = "REPRICE"
COMPLETE_MAKER = "COMPLETE_MAKER"
COMPLETE_TAKER = "COMPLETE_TAKER"
REDUCE = "REDUCE"
HOLD = "HOLD"

#: Every disposition considered, in a fixed order so a record is diffable.
ACTIONS = (PRESERVE, REPRICE, COMPLETE_MAKER, COMPLETE_TAKER, REDUCE, HOLD)

# ---- the OPENING family. `decide` above disposes of inventory we already
# hold; these are the actions available when the market is FLAT, and they
# are named separately because they ADD exposure rather than manage it.
OPEN = "OPEN"                            # the first exposure-taking round
REPLENISH = "REPLENISH"                  # another round after one completed
ACCUMULATE_FAVORED = "ACCUMULATE_FAVORED"    # more of the likelier side
ACQUIRE_UNFAVORED = "ACQUIRE_UNFAVORED"      # more of the cheaper side
OPENING_ACTIONS = (OPEN, REPLENISH, ACCUMULATE_FAVORED, ACQUIRE_UNFAVORED)

#: Every action one controller decides between. There is no second
#: controller: `decide_inventory` dispatches over this set and the pricing
#: lives in `decide` and `reopen_decision`, each called exactly once.
UNIFIED_ACTIONS = ACTIONS + OPENING_ACTIONS

#: The two that cross a book. Both are PRICED and REPORTED always, and both
#: are executable only while the owner's taker route is enabled -- which it
#: is not. REDUCE sells the held leg, which is also a crossing order.
CROSSING = (COMPLETE_TAKER, REDUCE)


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v if v.is_finite() else None
    try:
        x = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def taker_own_price(side: str, *, bid: Any, ask: Any) -> Optional[D]:
    """Where this side can be bought by CROSSING, in own terms.

    NOT `touch_own_price`, and the difference is a whole spread. That helper
    gives the MAKER touch -- where our resting quote would join the front of
    the queue -- so UP's is the YES bid. A taker LIFTS the other side: buying
    UP pays the YES ask, and buying DOWN means selling YES into the YES bid,
    so its own price is 1 - bid.

    This error was in my own first measurement of the hold-versus-complete
    comparison, which priced taker completions at the maker touch and so
    understated their cost by exactly the spread -- one tick in 217 of 220
    recorded samples.
    """
    b, a = _d(bid), _d(ask)
    if str(side).upper() == TS.UP:
        return a
    return None if b is None else (D(1) - b)


def held_sell_own_price(held_side: str, *, bid: Any, ask: Any
                        ) -> Optional[D]:
    """What a contract of the HELD side fetches if we sell it back.

    Selling UP hits the YES bid. Selling DOWN closes a short YES, which
    means buying YES at the ask, so its own-terms proceeds are 1 - ask.
    """
    b, a = _d(bid), _d(ask)
    if str(held_side).upper() == TS.UP:
        return b
    return None if a is None else (D(1) - a)


def taker_fee_usd(qty: Any, own_px: Any) -> Optional[D]:
    """The published schedule: fee = theta * C * p * (1 - p).

    `theta` is the TAKER coefficient. The schedule carries no maker-fee
    term, so a maker leg's fee is zero -- and a zero commission is NOT a
    rebate, so nothing is credited here either.
    """
    q, p = _d(qty), _d(own_px)
    if q is None or p is None or q < 0:
        return None
    return (TS.TAKER_THETA * q * p * (D(1) - p)).quantize(D("0.0001"))


def _act(name: str, *, executable: bool, worst: Optional[D] = None,
         best: Optional[D] = None, ev: Optional[D] = None,
         pair: Optional[D] = None, fee: Optional[D] = None,
         px: Optional[D] = None, qty: Optional[D] = None,
         queue_ahead: Optional[D] = None, note: str = "",
         infeasible: Optional[str] = None) -> Dict[str, Any]:
    return {
        "action": name,
        "feasible": infeasible is None,
        "infeasible_because": infeasible,
        "executable_now": bool(executable and infeasible is None),
        "crosses_book": name in CROSSING,
        "own_price": None if px is None else str(px),
        "qty": None if qty is None else str(qty),
        "pair_own_cost": None if pair is None else str(pair),
        "edge_per_contract": (None if pair is None
                              else str((D(1) - pair).quantize(D("0.0001")))),
        "fees_usd": None if fee is None else str(fee),
        "worst_case_usd": None if worst is None else str(
            worst.quantize(D("0.0001"))),
        "best_case_usd": None if best is None else str(
            best.quantize(D("0.0001"))),
        "ev_at_market_usd": None if ev is None else str(
            ev.quantize(D("0.0001"))),
        "queue_ahead_at_price": (None if queue_ahead is None
                                 else str(queue_ahead)),
        "note": note,
    }


def _rank_key(a: Mapping[str, Any]):
    """Worst case, then BEST case, then EV, then the shallower queue.

    BEST CASE IS THE SECOND KEY AND THAT IS NOT COSMETIC. Among maker
    actions the worst case is identical -- an unfilled quote costs nothing,
    so every one of them risks exactly the held cost -- and the best case is
    where the pair edge lives. Ranking on the queue before the pair chose a
    0.9800 pair over a resting 0.9500 one in the first draft of this module,
    which is precisely the defect PR #200 fixed. Missing figures sort last
    so an unpriced action can never outrank a priced one.
    """
    def num(k, default):
        v = a.get(k)
        return D(v) if v is not None else default
    return (-num("worst_case_usd", D("-999")),
            -num("best_case_usd", D("-999")),
            -num("ev_at_market_usd", D("-999")),
            # THE CHEAPER PAIR, BEFORE THE QUEUE. Giving every maker action
            # the same outcome envelope made worst, best and EV tie among
            # them -- correct, since an unfilled quote leaves us in HOLD
            # whichever one it is -- but it also made the PAIR invisible to
            # the ranking, and the queue then chose a WORSE pair. That is
            # the PR #200 defect arriving by a different door. A missing
            # pair sorts last so an unpriced action cannot outrank a priced
            # one.
            num("pair_own_cost", D("9")),
            num("queue_ahead_at_price", D("999999")))


def decide(*, market: Any, held_side: str, held_qty: Any,
           held_cost_usd: Any, deficient_qty: Any = 0,
           deficient_cost_usd: Any = 0,
           resting_own_px: Any = None, resting_qty: Any = None,
           resting_filled_qty: Any = 0,
           bid: Any, ask: Any, tick: Any, min_qty: Any,
           now_s: Any, window_start_s: Any, window_end_s: Any,
           book_age_s: Any = None, gap_suspected: bool = False,
           orders_uncertain: Any = None,
           residual_budget_usd: Any = None,
           queue_ahead_fn: Any = None,
           taker_enabled: bool = False,
           max_book_age_s: Any = D("2.0")) -> Dict[str, Any]:
    """Price every disposition of a confirmed unmatched leg. Read-only."""
    rec: Dict[str, Any] = {
        "label": "L5_UNMATCHED_DISPOSITION",
        "market": (None if market is None else str(market)),
        "held_side": str(held_side).upper(),
        "taker_route_enabled": bool(taker_enabled),
        "labels": ("the DECISION, not the order. Nothing here holds a "
                   "transport or an executor and nothing here submits. "
                   "`selected` is only ever an action whose executable_now "
                   "is true, so a disabled crossing route can be priced and "
                   "reported but never chosen. A maker action's upside is "
                   "conditional on a fill this module does not predict."),
    }

    def out(selected: Optional[str], reason: str,
            actions: Optional[List[Dict[str, Any]]] = None,
            **kw) -> Dict[str, Any]:
        acts = actions or []
        rec.update(kw)
        rec["actions"] = acts
        rec["selected"] = selected
        rec["selected_reason"] = reason
        rec["rejected"] = [
            {"action": a["action"],
             "why": (a["infeasible_because"] if not a["feasible"]
                     else ("NOT_EXECUTABLE_CROSSING_DISABLED"
                           if a["crosses_book"] and not a["executable_now"]
                           else "RANKED_BELOW_SELECTED")),
             "worst_case_usd": a["worst_case_usd"],
             "ev_at_market_usd": a["ev_at_market_usd"],
             "pair_own_cost": a["pair_own_cost"]}
            for a in acts if a["action"] != selected]
        # THE COUNTERFACTUAL, ALWAYS REPORTED. The whole point of pricing a
        # route we may not use is to say what declining it costs.
        best_any = None
        for a in acts:
            if a["feasible"] and a["worst_case_usd"] is not None:
                if best_any is None or (D(a["worst_case_usd"]) >
                                        D(best_any["worst_case_usd"])):
                    best_any = a
        sel = next((a for a in acts if a["action"] == selected), None)
        if (best_any is not None and sel is not None
                and sel.get("worst_case_usd") is not None):
            gap = D(best_any["worst_case_usd"]) - D(sel["worst_case_usd"])
            rec["counterfactual"] = {
                "best_feasible_action": best_any["action"],
                "executable_now": best_any["executable_now"],
                "worst_case_improvement_usd": str(gap.quantize(D("0.0001"))),
                "note": ("what the selected action gives up against the best "
                         "FEASIBLE one. A positive figure on a non-executable "
                         "action is the measured cost of the route being "
                         "switched off, and is never acted on here."),
            }
        return rec

    # ---------------------------------------------------- 0. FAIL CLOSED
    #
    # Each of these leaves the residual exactly where it is, which is HOLD,
    # and says so by name instead of going quiet. That is the whole point:
    # the 0100z failure was 83 records of `N/A`.
    if orders_uncertain:
        return out(HOLD, "ORDERS_UNCERTAIN",
                   [_act(HOLD, executable=True,
                         note="order state unresolved; decide nothing")])
    h, ch = _d(held_qty), _d(held_cost_usd)
    if h is None or ch is None:
        return out(HOLD, "INVENTORY_UNREADABLE",
                   [_act(HOLD, executable=True)])
    if h <= 0:
        return out(None, "NOTHING_UNMATCHED", [])
    b, a, tk, mq = _d(bid), _d(ask), _d(tick), _d(min_qty)
    if tk is None or tk <= 0:
        return out(HOLD, "TICK_UNKNOWN", [_act(HOLD, executable=True)])
    basis = ch / h                           # realised own cost per contract
    worst_hold = -ch
    best_hold = h - ch

    def hold_act(ev: Optional[D] = None) -> Dict[str, Any]:
        return _act(HOLD, executable=True, worst=worst_hold, best=best_hold,
                    ev=ev, qty=h,
                    note=("carry the residual to settlement. Binary: the "
                          "held side redeems $1.00 a share or $0.00."))

    if gap_suspected:
        return out(HOLD, "GAP_SUSPECTED", [hold_act()],
                   held_basis=str(basis.quantize(D("0.0001"))))
    age, maxage = _d(book_age_s), _d(max_book_age_s)
    if age is None or maxage is None or age < 0 or age > maxage:
        return out(HOLD, "BOOK_STALE", [hold_act()],
                   book_age_s=(None if age is None else str(age)),
                   held_basis=str(basis.quantize(D("0.0001"))))
    if b is None or a is None or b <= 0 or a <= 0 or a <= b:
        return out(HOLD, "BOOK_NOT_TWO_SIDED", [hold_act()],
                   held_basis=str(basis.quantize(D("0.0001"))))

    # ------------------------------------------------- 1. THE STATE, PRICED
    hs = str(held_side).upper()
    comp = TS.DOWN if hs == TS.UP else TS.UP     # the side that completes
    touch = TS.touch_own_price(comp, bid=b, ask=a)
    quote = TS.quote_own_price(comp, bid=b, ask=a, tick=tk,
                               depth_ticks=TS.depth_ticks())
    cap = TS.max_pair_cost()
    redeem_cap = TS.completion_pair_ceiling()
    # the market's own implied probability that our HELD side loses is the
    # complement's executable own price; it is the only calibrated
    # settlement probability available.
    p_other = touch
    ev_common = (h * (D(1) - p_other) - ch) if p_other is not None else None

    deadline = TS.may_complete(float(_d(now_s) or 0),
                               float(_d(window_start_s) or 0),
                               float(_d(window_end_s) or 0))
    runway = None
    try:
        runway = TS.cancel_cutoff(float(window_end_s)) - float(now_s)
    except BaseException:                                  # noqa: BLE001
        runway = None

    rec.update({
        "held_qty": str(h), "held_cost_usd": str(ch),
        "held_basis": str(basis.quantize(D("0.0001"))),
        "completing_side": comp,
        "book": {"bid": str(b), "ask": str(a),
                 "spread": str(a - b), "book_age_s": str(age),
                 "complement_touch_own": (None if touch is None
                                          else str(touch)),
                 "complement_quote_own": (None if quote is None
                                          else str(quote))},
        "ceilings": {"opening_pair_cap": str(cap),
                     "completion_pair_ceiling": str(redeem_cap)},
        "deadline": {"may_complete": deadline,
                     "runway_to_cancel_cutoff_s": (
                         None if runway is None else round(runway, 1))},
        "market_implied": {
            "p_held_side_loses": None if p_other is None else str(p_other),
            "source": "the complement's executable own price",
            "note": ("the only calibrated settlement probability available. "
                     "EV(hold) and EV(complete) are EQUAL at this price, by "
                     "identity, so expectancy cannot rank them."),
        },
    })

    def qa(px: Optional[D]) -> Optional[D]:
        if px is None or not callable(queue_ahead_fn):
            return None
        try:
            return _d(queue_ahead_fn(px))
        except BaseException as exc:                       # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    acts: List[Dict[str, Any]] = []

    # --------------------------------------------------------- 2. PRESERVE
    rest = _d(resting_own_px)
    rq, rfilled = _d(resting_qty), _d(resting_filled_qty) or D(0)
    working = (rq - rfilled) if (rq is not None) else None
    if rest is None:
        acts.append(_act(PRESERVE, executable=False,
                         infeasible="NO_RESTING_COMPLETION"))
    elif working is None or working <= 0:
        acts.append(_act(PRESERVE, executable=False,
                         infeasible="RESTING_ORDER_FULLY_FILLED"))
    elif (basis + rest) > redeem_cap:
        # KEEPING IT WOULD BE KEEPING A CERTAIN LOSS. A pair above
        # redemption pays less than it costs, so a fill is strictly bad and
        # PRESERVE stops dominating HOLD -- which is why this is refused
        # rather than merely ranked, so that HOLD and REPRICE can compete.
        # No conflict with PR #200 or #202: both are about preserving a
        # quote INSIDE the ceiling, and this is the other case.
        acts.append(_act(
            PRESERVE, executable=False, pair=(basis + rest), px=rest,
            infeasible="RESTING_PAIR_ABOVE_REDEMPTION"))
    else:
        pair_rest = basis + rest
        # THE TRUE OUTCOME ENVELOPE of keeping the quote working: it fills
        # and locks the pair, or it does not and we are in HOLD. So the
        # worst case equals holding's and the best is the better of the two
        # -- which is why PRESERVE can never rank below HOLD here. Both
        # PR #200 and PR #202 reached that inequality from their own
        # evidence; this is the same one.
        pair_out = h * (D(1) - pair_rest)
        acts.append(_act(
            PRESERVE, executable=True,
            worst=min(pair_out, worst_hold), best=max(pair_out, best_hold),
            ev=ev_common, pair=pair_rest, fee=D(0), px=rest, qty=working,
            queue_ahead=qa(rest),
            note=("leave the resting maker completion where it is. Worst "
                  "case equals holding; a fill instead locks %s, which is "
                  "not predicted here."
                  % pair_out.quantize(D("0.0001")))))

    # ---------------------------------------------------------- 3. REPRICE
    #
    # DEFERRED ENTIRELY to `completion_requote`, so the PR #200 pair ceiling
    # decides this and this module cannot contradict it.
    if rest is None or working is None or working <= 0:
        acts.append(_act(REPRICE, executable=False,
                         infeasible="NOTHING_RESTING_TO_REPRICE"))
    else:
        try:
            rq_out = TS.completion_requote(
                side=comp, resting_own_px=rest, resting_qty=rq,
                resting_filled_qty=rfilled, bid=b, ask=a, tick=tk,
                min_qty=mq, held_qty=h, held_cost_usd=ch,
                deficient_qty=deficient_qty,
                deficient_cost_usd=deficient_cost_usd,
                residual_budget_usd=residual_budget_usd,
                now_s=float(now_s), window_start_s=float(window_start_s),
                window_end_s=float(window_end_s), book_age_s=float(age),
                gap_suspected=False, orders_uncertain=None,
                queue_ahead_fn=queue_ahead_fn,
                require_queue_evidence=callable(queue_ahead_fn))
        except BaseException as exc:                       # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            rq_out = {"act": False, "reason":
                      "REQUOTE_UNREADABLE:%s" % type(exc).__name__}
        if not rq_out.get("act"):
            acts.append(_act(REPRICE, executable=False,
                             infeasible="REQUOTE_REFUSED:%s"
                                        % rq_out.get("reason")))
        else:
            tgt = _d(rq_out.get("to_price"))
            pair_tgt = basis + tgt if tgt is not None else None
            # THE SAME ENVELOPE AGAIN: a repriced maker quote that does
            # not fill also leaves us in HOLD.
            pair_out_rq = ((h * (D(1) - pair_tgt))
                           if pair_tgt is not None else None)
            acts.append(_act(
                REPRICE, executable=True,
                worst=(worst_hold if pair_out_rq is None
                       else min(pair_out_rq, worst_hold)),
                best=(None if pair_out_rq is None
                      else max(pair_out_rq, best_hold)),
                ev=ev_common, pair=pair_tgt, fee=D(0), px=tgt, qty=working,
                queue_ahead=qa(tgt),
                note=("cancel and re-place nearer the touch. Permitted by "
                      "`completion_requote`, which owns this decision.")))

    # --------------------------------------------------- 4. COMPLETE_MAKER
    if quote is None:
        acts.append(_act(COMPLETE_MAKER, executable=False,
                         infeasible="QUOTE_PRICE_UNDERIVABLE"))
    elif deadline is not None:
        acts.append(_act(COMPLETE_MAKER, executable=False,
                         infeasible="PAST_DEADLINE:%s" % deadline))
    else:
        pair_mk = basis + quote
        _resting_pair = (basis + rest) if rest is not None else None
        if pair_mk > redeem_cap:
            acts.append(_act(
                COMPLETE_MAKER, executable=False, pair=pair_mk, px=quote,
                infeasible="PAIR_ABOVE_COMPLETION_CEILING"))
        elif _resting_pair is not None and working is not None \
                and working > 0:
            # NOT AN ALTERNATIVE AT ALL WHILE A COMPLETION RESTS, and the
            # first live record is what settled this. There is one order per
            # side, so "place a fresh complement" over a resting one is not
            # a placement -- it is a CANCEL AND REPRICE, and REPRICE is the
            # action for that, owned by `completion_requote`.
            #
            # TREATING IT AS A PEER FORCED A COMPARISON I CANNOT MAKE. At
            # 1230z 12:30:44 the resting quote held the best bid at own
            # 0.4500 (pair 0.9500) with queue_ahead 0, against a fresh one
            # at 0.3500 (pair 0.8500) behind 2141 contracts. Better pair,
            # far worse queue. Ranking pair-before-queue picks the deep
            # one; queue-before-pair picks a WORSE pair elsewhere. Both are
            # wrong, because weighing a pair against a queue needs a fill
            # probability and `l5_queue_depletion` establishes that we do
            # not have one.
            #
            # So the scalar comparison is removed rather than guessed at.
            # One owner per decision is the whole architecture of PRs #200,
            # #202 and #205.
            acts.append(_act(
                COMPLETE_MAKER, executable=False, pair=pair_mk, px=quote,
                infeasible="RESTING_COMPLETION_EXISTS_REPRICE_OWNS_IT"))
        else:
            # THE SAME OUTCOME ENVELOPE AS PRESERVE, and the 1230z live
            # record is why. A maker complement that does not fill leaves
            # us in HOLD exactly as a preserved quote does, so its best
            # case is the better of the pair and the hold upside. Reporting
            # only the pair outcome made PRESERVE's best 0.5000 against
            # this one's 0.1500 on an ACCOUNTING asymmetry rather than on
            # economics: at 12:30:44 PRESERVE won the ranking on that
            # difference, when the honest discriminator was the queue --
            # 0 ahead for the resting order against 2141 for a new one.
            # Same answer, sound reason.
            pair_out_mk = h * (D(1) - pair_mk)
            acts.append(_act(
                COMPLETE_MAKER, executable=True,
                worst=min(pair_out_mk, worst_hold),
                best=max(pair_out_mk, best_hold),
                ev=ev_common, pair=pair_mk,
                fee=D(0), px=quote, qty=h, queue_ahead=qa(quote),
                note=("place a fresh maker complement %s ticks behind the "
                      "touch. No fill is predicted." % TS.depth_ticks())))

    # --------------------------------------------------- 5. COMPLETE_TAKER
    #
    # PRICED ALWAYS, EXECUTABLE ONLY WITH THE OWNER'S ROUTE. This is the
    # counterfactual the 15-window measurement is about, and computing it is
    # what lets the record state the cost of leaving the route off.
    cross_px = taker_own_price(comp, bid=b, ask=a)
    if cross_px is None:
        acts.append(_act(COMPLETE_TAKER, executable=False,
                         infeasible="CROSS_PRICE_UNDERIVABLE"))
    else:
        fee = taker_fee_usd(h, cross_px) or D(0)
        pair_tk = basis + cross_px
        certain = h * (D(1) - pair_tk) - fee
        # DEADLINE FIRST, matching COMPLETE_MAKER. Both bounds can bind at
        # once and the record should name the time bound when the window has
        # closed, since no price can cure that one.
        infeasible = None
        if deadline is not None:
            infeasible = "PAST_DEADLINE:%s" % deadline
        elif pair_tk > redeem_cap:
            infeasible = "PAIR_ABOVE_COMPLETION_CEILING"
        acts.append(_act(
            COMPLETE_TAKER, executable=bool(taker_enabled),
            worst=certain, best=certain, ev=certain, pair=pair_tk, fee=fee,
            px=cross_px, qty=h, queue_ahead=D(0), infeasible=infeasible,
            note=("LIFT the complement, paying the far side -- a whole "
                  "spread dearer than the maker touch. CERTAIN outcome, so "
                  "worst equals best. Counterfactual while the owner's "
                  "taker route is off; never submitted from here.")))

    # ---------------------------------------------------------- 6. REDUCE
    #
    # Selling the held leg also crosses, so it carries the same gate.
    sell_px = held_sell_own_price(hs, bid=b, ask=a)
    if sell_px is None:
        acts.append(_act(REDUCE, executable=False,
                         infeasible="SELL_PRICE_UNDERIVABLE"))
    else:
        rfee = taker_fee_usd(h, sell_px) or D(0)
        rcertain = h * sell_px - ch - rfee
        acts.append(_act(
            REDUCE, executable=bool(taker_enabled),
            worst=rcertain, best=rcertain, ev=rcertain, fee=rfee,
            qty=h, px=sell_px, queue_ahead=D(0),
            infeasible=(None if deadline is None
                        else "PAST_DEADLINE:%s" % deadline),
            note=("sell the held leg back at its own bid, realising the "
                  "loss instead of pairing it. Crosses the book, so it "
                  "answers to the same owner route as COMPLETE_TAKER and "
                  "is priced for comparison only.")))

    # ------------------------------------------------------------ 7. HOLD
    acts.append(hold_act(ev_common))

    # --------------------------------------------------------- 8. SELECT
    order = {n: i for i, n in enumerate(ACTIONS)}
    acts.sort(key=lambda x: order[x["action"]])

    # HOLD IS THE FALLBACK, NOT A COMPETITOR, and the reason is structural:
    # an unfilled maker quote leaves us in exactly the HOLD state, so a
    # maker action whose pair is INSIDE redemption weakly DOMINATES holding.
    # A quote whose pair is ABOVE redemption does not dominate -- a fill
    # there is a certain loss -- and is refused above rather than ranked,
    # so HOLD competes in exactly that case -- same downside, plus a chance
    # of a pair. Ranking HOLD on the same scale picked it over a resting
    # 0.9500 pair in an earlier draft, because holding's best case is the
    # whole $1.00 windfall; that is the upside we keep anyway if the quote
    # does not fill. The same inequality is what PR #200 and PR #202 each
    # established from their own evidence: withdrawing a quote inside the
    # ceiling is never an improvement.
    pool = [x for x in acts
            if x["executable_now"] and x["action"] != HOLD
            and x["worst_case_usd"] is not None]
    if not pool:
        return out(HOLD, "NO_EXECUTABLE_ALTERNATIVE", acts)
    best = sorted(pool, key=_rank_key)[0]
    return out(best["action"],
               "BEST_WORST_CASE_THEN_BEST_PAIR_AMONG_EXECUTABLE", acts)


# ======================================================= THE REOPENING CHOICE
#
# 1230z IS WHY THIS EXISTS, and the owner's reading of the mechanism was
# exactly right. `L5_TS_ROUND_ALLOCATIONS` splits the CASH ENVELOPE across
# cycles; the number of opening rounds is `acc_max_rounds()`, and
# `probe_round` walked to the first incomplete round with NO ECONOMICS AT
# ALL. So after round 0 matched its pair at 12:30:49, round 1 opened at
# 12:32:54 -- UP 0.4600 and DOWN 0.4900 together. The UP filled at 12:33:57,
# the DOWN never did and was cancelled at 12:34:58, and the window settled:
#
#     round 0, pair at 0.9550      +$0.0450
#     round 1, unmatched UP        -$0.4550
#     settled                      -$0.4100
#
# The system behaved as implemented. What was missing was a decision.
#
# WHAT THIS IS NOT. It is not a cap of one pair per window -- the owner's
# objective is REPEATED profitable completion, and a counter cannot tell a
# good round from a bad one. It is not a new controller either: it lives
# beside `decide` in the authoritative disposition module and is consulted
# by the existing opening path.
#
# AND IT WILL NOT FINANCE A RESIDUAL OUT OF A LOCKED PAIR. "We are +$0.045
# up, so a -$0.455 risk is affordable" is the reasoning that lost the
# window, and it is wrong twice: the locked profit is a tenth of the risk,
# and netting them spends a realized gain as a loss reserve. Locked profit
# is REPORTED here and is deliberately NOT part of either test.

#: THE PAIRING RATE, from OUR OWN venue-confirmed orders.
#:
#: These replace the recorded-book PROVEN figures (0.525 / 0.423) that this
#: gate shipped with. PROVEN counts a leg filled only when the OPPOSITE
#: touch reached our price -- the market moved THROUGH us -- so it samples
#: exactly the adverse case and understates pairing, as its own docstring
#: said. `analysis/l5/issue206/own_order_lifecycles.py` measures our 34
#: venue-confirmed orders instead: 8 of 11 rounds that saw a first fill went
#: on to PAIR, and all three that did not were singles WE created by
#: cancelling the complement, none by the market refusing us.
REOPEN_P_PAIR = D("0.727")
REOPEN_P_SINGLE = D("0.273")

#: WHAT AN UNMATCHED LEG IS ACTUALLY WORTH, measured on the REPAIRED
#: dataset. This constant has now been wrong twice in opposite directions
#: and both errors came from incomplete data rather than from bad algebra.
#:
#: FIRST IT WAS -0.2440, from `hold $` in QUEUE_DEPLETION_FINDINGS.md:
#: recorded books under PROVEN fills, where a leg counts filled only when
#: the opposite touch CROSSED our price. Every leg in that sample filled
#: because the market moved against it, so the figure measured the sampling
#: rule. At 6.4x the pair edge it refused every reopening.
#:
#: THEN IT WAS 0, from L5's own fills -- but on the 186 windows of
#: `data/l5/raw/activity.jsonl` whose redemption identity held, and that
#: file was itself missing rows. It put a residual contract at +0.026 to
#: +0.037, so zero looked conservative.
#:
#: THE REPAIRED UNION SAYS OTHERWISE. `analysis/l5/issue206/
#: l5_repair_dataset.py` unions every capture in the repository --
#: `activity_gap.jsonl.gz` alone adds 691,077 rows the offset-paged file
#: had lost -- giving 902,016 deduped trades against 70,435, and 4,720
#: COMPLETE btc15m windows against 408. Cross-checked against the separate
#: `/trades` endpoint capture: 10,183 rows, 100% present, none missing.
#:
#:     matched   5,089,010 contracts  basis 0.9650  +0.0350 / pair
#:     residual  1,240,600 contracts  basis 0.3205  -0.0236 / contract
#:
#: So a residual LOSES about 2.4 cents a contract, and the +0.026..+0.037
#: that the small sample showed was selection: those 186 windows were the
#: quiet ones. The matched figure is unchanged across every cut ever
#: measured (+0.0356, +0.0359, +0.0350), which is why the repair is
#: believed rather than the convenient half of it.
#:
#: THIS IS THE MEASURED VALUE, NOT A NEUTRAL PRIOR. Zero is no longer
#: conservative -- it is optimistic -- so the hypothesis is retired in
#: favour of the measurement, over a sample 60x larger than the one that
#: produced zero.
#:
#: Throughput survives it. At the deployed d=2:
#:     E = 0.727*0.0500 + 0.273*(-0.0236) = +0.0299, above the floor.
#: The gate still prices repeated rounds rather than forbidding them, and
#: the WORST CASE is untouched: capacity still reserves the whole leg.
REOPEN_SINGLE_OUTCOME_USD = D("-0.0236")

#: How much better than break-even an incremental round must look before it
#: is worth opening fresh exposure. One tick on a one-contract pair.
REOPEN_MIN_EDGE_USD = D("0.01")

#: THE FLOOR IS "ONE TICK ON A ONE-CONTRACT PAIR", and it was being compared
#: with the expectancy of whatever size the round happens to be.
#:
#: MEASURED, 2026-11-10 2145z, round 7: qty 0.21, pair edge 0.21 * 0.05 =
#: $0.0105, expectancy 0.727 * 0.0105 + 0.273 * (0.21 * -0.0236) = $0.0063,
#: refused against $0.01. Per contract that is +$0.0299 -- the very figure
#: the note above `REOPEN_SINGLE_OUTCOME_USD` says clears the floor. At the
#: deployed d=2 and a one-tick spread the expectancy is 0.0299 a contract,
#: so no round under 0.335 contracts can ever pass, and production clips are
#: 0.21 to 0.31: every reopening is refused whatever the book does.
#:
#: Exactly "ON" compares like with like: the floor is one cent PER CONTRACT
#: of the proposed round, so it scales down for a clip under one contract
#: and UP for a larger one. The probabilities, the adverse-selection figure
#: and every capacity bound are untouched, and an expectancy at or below
#: zero still refuses at any size. Anything else is the comparison every
#: earlier deployment made.
REOPEN_FLOOR_PER_CONTRACT_ENV = "L5_REOPEN_FLOOR_PER_CONTRACT"


def reopen_floor_per_contract() -> bool:
    return str(os.getenv(REOPEN_FLOOR_PER_CONTRACT_ENV, "")).strip() == "ON"

#: OUR OWN SETTLED RECORD OUTRANKS A PRIOR, ONCE THERE IS ENOUGH OF IT.
#:
#: The expectancy above multiplies two things this program has never
#: measured on its own money: `pair_edge` is the PLANNED edge, 1 minus the
#: pair cost the legs were quoted at, and the single-leg figure is L5's
#: residual. Three independent records now disagree with both, in the same
#: direction (`analysis/l5/PAIR_COMPLETION_REPLAY.md`):
#:
#:   * every pair this engine has completed since the pair ceiling settled
#:     at 0.96 -- 2015z, 0200z, 1300z -- completed at 0.987-0.99, not 0.95;
#:   * a path replay over 526 settled windows puts the median completed pair
#:     at 0.99, 7% of pairs at plan, 25% of first fills stranded at -0.33,
#:     and the result at -0.073 per first fill, negative in every month,
#:     at every depth and for every completion ceiling;
#:   * the shadow controller on live PMUS books measured -0.083 per filled
#:     contract over 956 fills, 92% of them adverse.
#:
#: The mechanism is one sentence: a resting leg fills when the market trades
#: THROUGH it, so it is already under water at the fill (4.5 cents, mean),
#: and the complement is then only reachable near redemption.
#:
#: So a further round must ALSO clear what the ledger says rounds have
#: actually paid, per contract that took a first fill. This can only REFUSE:
#: a favourable record never rescues a round the arithmetic above declined.
#: Below this many settled windows the record is too thin to listen to and
#: the decision stands on its priors, exactly as before.
OWN_EVIDENCE_MIN_WINDOWS = 20

#: A PAIR LADDERED BESIDE RESTING ONES NEEDS THE RECORD TO SAY YES.
#:
#: A further round opened one at a time is refused by a losing record and
#: allowed by a thin one. A CONCURRENT round is held to more: it multiplies
#: the orders exposed to the same move, so it opens only when the ledger's
#: own settled record is usable AND clears the floor. No record is not
#: permission to ladder.
#:
#: THE OWNER MAY WAIVE THE RECORD, and only the owner, by setting this to
#: the exact token below -- for a bounded validation of the mechanism, with
#: the expected value stated. It waives this stage alone: the arithmetic
#: above, the capacity test and every limit still bind, and every decision
#: made under it says so.
#: THE FIRST OPENING OF A WINDOW IS PRICED LIKE EVERY OTHER. Exactly "ON" or
#: it is off.
#:
#: `probe_reopen_decision` returned `reopen: True` for the first
#: exposure-taking round before any economics were consulted, so the one
#: order every window always sent was the one order nothing had priced:
#: not the capacity test, not the expectancy floor, and not what our own
#: settled windows on this venue have paid. On 108 settled markets with a
#: measured cost (2026-10-25 to 11-11) a first-leg contract paired 62% of
#: the time for +5.6c and stranded 38% of the time for -7.0c -- +0.8c a
#: contract overall, -2.4c over the later half -- and each window opened
#: regardless.
#:
#: With the switch the first round goes through `reopen_decision` exactly
#: as a later round does, so it must clear the same three tests, and the
#: owner's `L5_TS_OWN_EVIDENCE_GATE=WAIVED_BY_OWNER` applies to it exactly
#: as it does to them. It can only REFUSE an opening: a leg already claimed
#: is a completion and is never priced here, and nothing about inventory
#: already held changes.
FIRST_OPENING_PRICED_ENV = "L5_TS_FIRST_OPENING_PRICED"


def first_opening_priced() -> bool:
    return (os.getenv(FIRST_OPENING_PRICED_ENV) or "") == "ON"


OWN_EVIDENCE_GATE_ENV = "L5_TS_OWN_EVIDENCE_GATE"
OWN_EVIDENCE_WAIVER_TOKEN = "WAIVED_BY_OWNER"


def own_evidence_gate_waived() -> bool:
    """True only for the exact token. A typo leaves the gate enforced."""
    return (os.getenv(OWN_EVIDENCE_GATE_ENV) or "").strip() == \
        OWN_EVIDENCE_WAIVER_TOKEN


#: A round needs time to place two legs, see one fill, and work the other.
#: Below this the second leg cannot realistically be completed.
REOPEN_MIN_RUNWAY_S = 180.0


def reopen_decision(*, market: Any, rounds_used: int,
                    locked_pair_pnl_usd: Any = None,
                    worst_leg_own_px: Any,
                    bid: Any, ask: Any, tick: Any,
                    bid_shares: Any = None, ask_shares: Any = None,
                    qty: Any = D(1),
                    now_s: Any, window_end_s: Any,
                    exposure_headroom_usd: Any,
                    outstanding_worst_case_usd: Any = D(0),
                    daily_loss_room_usd: Any = None,
                    depth_ticks: Optional[int] = None,
                    p_pair: Any = None, p_single: Any = None,
                    single_outcome_usd: Any = None,
                    min_edge_usd: Any = None,
                    own_evidence: Optional[Mapping[str, Any]] = None,
                    concurrent: bool = False) -> Dict[str, Any]:
    """Is ANOTHER opening round economically justified? Read-only.

    Returns `{"reopen": bool, "reason": str, ...}`. `reopen` is True only
    when every hard bound holds AND incremental expectancy clears the floor.
    Anything unreadable refuses, because an unknown is not a zero and the
    safe direction for OPENING exposure is to stop.

    SCOPE, stated precisely: this gates opening a NEW round only. It never
    gates completing, repricing or preserving inventory already held --
    those are `decide`'s actions and a residual must still be worked after
    a refusal here. A refusal stops us ADDING risk, not managing it.

    THE TWO TESTS, in this order, because a hard bound is not an opinion.

      1. CAPACITY, on the worst case. Both legs fill and one settles
         worthless: `qty * worst_leg_own_px`, and `worst_leg_own_px` is
         the DEARER of the round's two legs, because which one fills alone
         is not ours to choose. That must fit what remains of
         the aggregate exposure cap AND the daily realized-loss room, with
         every outstanding order counted as if it filled. This is the
         owner's bound and expectancy is never traded against it.

      2. EXPECTANCY, on measured quantities only:

             E = p_pair * pair_edge  +  p_single * single_outcome

         The pair identity gives `pair_cost = 1 - spread - 2*d*tick`, so
         `pair_edge = qty * (spread + 2*d*tick)` exactly. `single_outcome`
         is the measured adverse-selection constant, NOT the leg's cost --
         see `REOPEN_SINGLE_OUTCOME_USD` for why those differ. The round
         opens only if `E > min_edge_usd`.

    WHAT THAT ARITHMETIC SAYS TODAY, and it is not the comfortable answer.
    At the deployed `d=2` with the usual 1-tick spread:

        pair_edge = 0.01 + 0.04                   = 0.0500
        E         = 0.525*0.05 + 0.423*(-0.2440)  = -0.0770

    Refused -- and 1230z round 1 is refused by exactly this, with no
    reference to the +$0.0450 already locked. Inverting it gives the number
    worth quoting: reopening pays only once an unmatched leg costs less
    than **$0.0384**, against $0.2440 measured. A factor of 6.4.

    So this gate currently refuses every maker-only reopening at the
    deployed depth, and that is the correct reading of the evidence rather
    than a cap: the record always reports `breakeven_single_outcome_usd`,
    and the moment pairing improves, the spread widens, depth increases or
    adverse selection is measured smaller, the same arithmetic permits it.
    No depth in either sample reaches the floor on maker-only economics
    (best is +$0.0020 at `d=6` in sample), which is the evidence this
    program needs next, not a parameter to relax.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, reopen=False, reason=reason, market=(
            None if market is None else str(market)))

    pp = _d(p_pair) if p_pair is not None else REOPEN_P_PAIR
    ps = _d(p_single) if p_single is not None else REOPEN_P_SINGLE
    single = (_d(single_outcome_usd) if single_outcome_usd is not None
              else REOPEN_SINGLE_OUTCOME_USD)
    floor = (_d(min_edge_usd) if min_edge_usd is not None
             else REOPEN_MIN_EDGE_USD)
    q = _d(qty)
    leg = _d(worst_leg_own_px)
    b, a, tk = _d(bid), _d(ask), _d(tick)
    cap_room = _d(exposure_headroom_usd)
    outstanding = _d(outstanding_worst_case_usd)
    locked = _d(locked_pair_pnl_usd)

    # ---- STRUCTURAL READS FIRST, AND FOR EVERY ROUND. A round cannot be
    #      called justified on a book, tick or size we cannot read, whether
    #      it is the first or the fifth -- `decide_inventory` reports this
    #      verdict as an ACTION, and "OPEN into a one-sided book" is not one.
    #      The ECONOMIC tests below still exempt the first round.
    try:
        used_rounds = int(rounds_used)
    except (TypeError, ValueError):
        return no("ROUNDS_USED_UNREADABLE")
    if q is None or q <= 0 or leg is None or leg <= 0:
        return no("ROUND_SIZE_OR_PRICE_UNREADABLE")
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if b is None or a is None or b <= 0 or a <= 0 or a <= b:
        return no("BOOK_NOT_TWO_SIDED")

    # ---- the FIRST round is not a reopening and the ECONOMICS never gate
    #      it. Gating round 0 would turn the strategy off, which is not what
    #      "decide whether to reopen" asks for, and the executor's own cash,
    #      deficit and exposure gates already own the first leg.
    # WITH `L5_TS_FIRST_OPENING_PRICED=ON` IT IS PRICED BELOW LIKE ANY OTHER
    # ROUND. That can turn the strategy off, and it should when the tests
    # below say an opening is not worth making: the first round is the one
    # exposure every window takes, and it was the only one nothing priced.
    if used_rounds <= 0 and not first_opening_priced():
        return dict(reopen=True, reason="FIRST_ROUND_NOT_A_REOPENING",
                    market=(None if market is None else str(market)),
                    rounds_used=used_rounds,
                    economics_not_evaluated_for_the_first_round=True)

    if cap_room is None:
        return no("EXPOSURE_BUDGET_UNREADABLE")
    if outstanding is None:
        return no("OUTSTANDING_EXPOSURE_UNREADABLE")
    if pp is None or ps is None or single is None or floor is None:
        return no("ECONOMIC_PRIORS_UNREADABLE")
    try:
        d_ticks = int(depth_ticks) if depth_ticks is not None \
            else int(TS.depth_ticks())
    except BaseException:
        return no("DEPTH_UNREADABLE")
    if d_ticks < 0:
        return no("DEPTH_UNREADABLE")

    # ---- 1. CAPACITY. An unmatched leg is the worst case of a round, and
    #         every live order is counted as if it filled.
    worst_case = q * leg
    headroom = cap_room - outstanding
    rec: Dict[str, Any] = {
        "market": (None if market is None else str(market)),
        "rounds_used": used_rounds,
        "qty": str(q), "worst_leg_own_px": str(leg),
        "bid": str(b), "ask": str(a), "spread": str(a - b),
        "depth_ticks": d_ticks,
        "worst_case_usd": str(worst_case.quantize(D("0.0001"))),
        "exposure_headroom_gross_usd": str(cap_room),
        "outstanding_worst_case_usd": str(outstanding),
        "exposure_headroom_usd": str(headroom.quantize(D("0.0001"))),
        "daily_loss_room_usd": (None if _d(daily_loss_room_usd) is None
                                else str(_d(daily_loss_room_usd))),
        # REPORTED, NOT NETTED. A realized gain is not a loss reserve, and
        # "we are up $0.045 so a $0.455 risk is affordable" is the
        # reasoning that lost 1230z. It appears here so a reader can see it
        # was considered and deliberately excluded from both tests.
        "locked_pair_pnl_usd": (None if locked is None else str(locked)),
        "locked_pnl_is_not_a_loss_reserve": True,
    }

    if headroom < worst_case:
        return no("EXPOSURE_HEADROOM_BELOW_WORST_CASE", **rec)
    if daily_loss_room_usd is not None:
        room = _d(daily_loss_room_usd)
        if room is None:
            return no("DAILY_LOSS_ROOM_UNREADABLE", **rec)
        if room < worst_case:
            return no("DAILY_LOSS_ROOM_BELOW_WORST_CASE", **rec)

    # ---- runway: two legs, a fill and a completion all need time
    try:
        runway = float(window_end_s) - float(now_s)
    except (TypeError, ValueError):
        return no("WINDOW_UNREADABLE", **rec)
    rec["runway_s"] = round(runway, 1)
    if runway < REOPEN_MIN_RUNWAY_S:
        return no("INSUFFICIENT_RUNWAY_FOR_A_ROUND",
                  required_s=REOPEN_MIN_RUNWAY_S, **rec)

    # ---- displayed depth on both sides, so a round is never opened into a
    #      book that cannot absorb either leg
    bs, as_ = _d(bid_shares), _d(ask_shares)
    if bs is not None and bs < q:
        return no("BID_DEPTH_BELOW_ROUND_SIZE", displayed=str(bs), **rec)
    if as_ is not None and as_ < q:
        return no("ASK_DEPTH_BELOW_ROUND_SIZE", displayed=str(as_), **rec)

    # ---- 2. EXPECTANCY, on the pair identity and the measured constants
    spread = a - b
    pair_edge = q * (spread + (2 * D(d_ticks) * tk))
    single_total = q * single
    expectancy = (pp * pair_edge) + (ps * single_total)
    _per_contract = reopen_floor_per_contract()
    _floor_one_contract = floor
    if _per_contract:
        floor = floor * q
    # the number worth quoting: how cheap an unmatched leg must be for this
    # round to pay. Always reported, including on a refusal.
    breakeven = (((floor - (pp * pair_edge)) / ps) / q) if ps > 0 else None
    rec.update({
        "p_pair": str(pp), "p_single": str(ps),
        "single_outcome_usd_per_contract": str(single),
        "prior_source": ("QUEUE_DEPLETION_FINDINGS.md section 4, d=2 out "
                         "of sample over 13 recorded windows; pairing is a "
                         "LOWER bound and the single-leg cost an UPPER "
                         "one. Never derived from the five 1230z orders. "
                         "Maker-only: the cheaper taker disposition is "
                         "disabled by owner order and is not assumed."),
        "pair_edge_usd": str(pair_edge.quantize(D("0.0001"))),
        "single_leg_outcome_usd": str(single_total.quantize(D("0.0001"))),
        "worst_case_is_not_expectancy": True,
        "expectancy_usd": str(expectancy.quantize(D("0.0001"))),
        "min_edge_usd": str(floor),
        "min_edge_basis": ("PER_CONTRACT_OF_THIS_ROUND" if _per_contract
                           else "FIXED_DOLLARS_WHATEVER_THE_SIZE"),
        "min_edge_one_contract_usd": str(_floor_one_contract),
        "breakeven_single_outcome_usd": (
            None if breakeven is None
            else str(breakeven.quantize(D("0.0001")))),
        "labels": ("the reopening DECISION, not a cap and not a realized "
                   "result. Capacity is a hard bound and is never traded "
                   "against expectancy. Expectancy uses the pair identity "
                   "1 - spread - 2*d*tick and measured adverse selection; "
                   "no fill probability is invented. A refusal stops "
                   "OPENING a new round only -- inventory already held is "
                   "still worked by `decide`."),
    })
    if expectancy <= floor:
        return no("INCREMENTAL_EXPECTANCY_BELOW_FLOOR", **rec)

    # ---- 3. OUR OWN SETTLED RECORD, when there is enough of it. Reached
    #         only by a round the priors already permitted, so it can only
    #         refuse. See OWN_EVIDENCE_MIN_WINDOWS.
    own = own_settled_verdict(own_evidence, qty=q, floor=floor)
    waived = own_evidence_gate_waived()
    rec["own_settled_evidence"] = own
    rec["own_evidence_gate"] = ("WAIVED_BY_OWNER" if waived else "ENFORCED")
    rec["concurrent_round"] = bool(concurrent)
    if not waived:
        if own["usable"] and not own["clears_floor"]:
            return no("OWN_SETTLED_EXPECTANCY_BELOW_FLOOR", **rec)
        if concurrent and not own["usable"]:
            return no("CONCURRENT_ROUND_NEEDS_OWN_SETTLED_EVIDENCE", **rec)
    return dict(rec, reopen=True, reason=None)


def entry_economics(own_evidence: Optional[Mapping[str, Any]], *,
                    floor_per_contract: Any) -> Dict[str, Any]:
    """This venue's conditional opening economics, or UNKNOWN. Read-only.

    Feeds `l5_entry_economics.evaluate_opening` with the three figures the
    settled record states -- P(pair | first fill), pair net and unmatched
    net, per contract -- and nothing else. No book, no queue position, no
    fill model, no fee or rebate beyond what the venue settled. UNKNOWN
    when the record does not state them; never a default.
    """
    try:
        from src import l5_entry_economics as EE
        c = (own_evidence or {}).get("conditional")
        if not isinstance(c, Mapping) or c.get("status") != "MEASURED":
            return {"status": "UNKNOWN",
                    "reason": ("RECORD_DOES_NOT_STATE_THE_SPLIT:%s" % (
                        (c or {}).get("reason") if isinstance(c, Mapping)
                        else "ABSENT"))[:120]}
        out = dict(EE.evaluate_opening(
            completed_net_per_contract=c.get("pair_net_per_contract"),
            residual_net_per_contract=c.get("unmatched_net_per_contract"),
            completion_probability_given_first_fill=c.get(
                "p_pair_given_first_fill"),
            minimum_net_edge_per_contract=floor_per_contract))
        out["source"] = "OWN_VENUE_SETTLED_WINDOWS"
        out["first_leg_contracts"] = c.get("first_leg_contracts")
        return out
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"status": "UNKNOWN",
                "reason": "ENTRY_ECONOMICS_RAISED:%s" % type(exc).__name__}


def own_settled_verdict(own_evidence: Optional[Mapping[str, Any]], *,
                        qty: Any, floor: Any) -> Dict[str, Any]:
    """What our settled windows say one more round of `qty` is worth.

    `usable` is False -- and the caller ignores this -- when no record was
    supplied, when it holds fewer than OWN_EVIDENCE_MIN_WINDOWS windows, or
    when any figure in it is unreadable. An unreadable record is NOT read as
    a favourable one: it simply does not vote, and the priors' verdict
    stands on its own.
    """
    out: Dict[str, Any] = {"usable": False, "clears_floor": None,
                           "min_windows": OWN_EVIDENCE_MIN_WINDOWS,
                           "windows": None, "net_per_contract": None,
                           "expectancy_usd": None, "why_not_used": None}
    if not isinstance(own_evidence, Mapping):
        return dict(out, why_not_used="NO_RECORD_SUPPLIED")
    try:
        n = int(own_evidence.get("windows"))
    except (TypeError, ValueError):
        return dict(out, why_not_used="WINDOW_COUNT_UNREADABLE")
    out["windows"] = n
    if n < OWN_EVIDENCE_MIN_WINDOWS:
        return dict(out, why_not_used="TOO_FEW_SETTLED_WINDOWS")
    per, q, fl = _d(own_evidence.get("net_per_contract")), _d(qty), _d(floor)
    if per is None or q is None or fl is None:
        return dict(out, why_not_used="RECORD_UNREADABLE")
    e = q * per
    clears = bool(e > fl)
    # THE SAME RECORD, STATED AS CONDITIONAL ECONOMICS, by the independent
    # calculator of PR #220: how often a first fill paired on this venue,
    # what a pair and an unmatched contract paid, and the pairing rate at
    # which an opening breaks even. It can only AGREE or REFUSE: when it
    # can compute a figure and that figure does not clear the floor, the
    # opening is not cleared whatever the aggregate says; when it cannot
    # (the record does not state the split), it does not vote. Its own
    # `admissible` is never read -- nothing here authorizes an order.
    entry = entry_economics(own_evidence, floor_per_contract=(
        fl / q if q > 0 else None))
    if entry.get("status") == "SCENARIO_ONLY" and \
            entry.get("scenario_clears_edge") is not True:
        clears = False
    return dict(out, usable=True, why_not_used=None,
                net_per_contract=str(per),
                expectancy_usd=str(e.quantize(D("0.0001"))),
                clears_floor=clears,
                entry_economics=entry,
                windows_with_a_pair=own_evidence.get("windows_with_a_pair"),
                windows_with_a_stranded_leg=own_evidence.get(
                    "windows_with_a_stranded_leg"),
                span=[own_evidence.get("from"), own_evidence.get("to")])


# ============================================ UNEQUAL INVENTORY, PRICED
#
# WHAT PHASE 3 ASKED FOR: accumulate the favoured side when an independently
# estimated probability supports it, and acquire the unfavoured side when
# that is economically justified. Unequal inventory allowed when its
# conservative EV and worst case justify it.
#
# THE ARITHMETIC SAYS WHAT TO LOOK FOR. Let the market's own implied
# probability of UP be p, and rest each leg `delta` behind its touch, so
# u = p - delta and d = (1 - p) - delta. For n_up UP and n_dn DOWN settling
# at S in {0, 1}:
#
#     net = n_up*S + n_dn*(1 - S) - n_up*u - n_dn*d
#
#     BALANCED (1,1):  net = 1 - (u + d) = 2*delta, for EITHER outcome.
#     FAVOURED (2,1):  EV  = p + 1 - 2u - d        = 3*delta.
#     worst case (2,1) when the favourite loses:   = 3*delta - p.
#
# So under a fair market EVERY LEG EARNS ITS OWN DELTA and expectancy scales
# with the TOTAL contract count, balanced or not. Unequal inventory buys no
# extra expectancy per contract -- it buys VARIANCE, and a worst case that
# grows with the favourite's own price. 1230z is that worst case: UP 2 /
# DOWN 1 paid 1.4100 for a 1.0000 payout.
#
# AND WE HAVE NO QUALIFYING SIGNAL. "Independently estimated" has to mean
# independent of the price, because the mid IS the market's probability and
# weighting toward it carries no information the price does not already
# hold. The only outside signal in our recordings is top-of-book size
# imbalance, and `analysis/l5/issue195/inventory_weighting_compare.py`
# measures it directly: where imbalance DISAGREES with the mid it calls the
# direction correctly 14.0% of the time in sample and 20.1% out of sample.
# It is strongly ANTI-predictive -- resting size is supply of liquidity and
# the market trades through it. Weighted by it, favourite accumulation was
# the worst of five variants in both samples (-0.1573 against balanced's
# -0.0774 per attempt out of sample) and doubled the worst case.
#
# So this function PRICES the action and refuses it for want of a signal,
# rather than the engine being structurally unable to consider it. When a
# calibrated independent estimate exists, pass it and the same arithmetic
# admits the trade.

#: How much better than balanced an unequal round must look, per contract,
#: before taking directional risk. Unequal inventory must EARN its variance.
WEIGHT_MIN_EDGE_USD = D("0.01")

#: A probability estimate must beat the market's own mid by at least this
#: much to count as information. Below it, we are paying spread to express
#: the price's own opinion.
WEIGHT_MIN_EDGE_PROB = D("0.05")


def weighted_inventory_decision(*, market: Any, favoured_side: Any,
                                n_favoured: Any, n_other: Any,
                                up_own_px: Any, down_own_px: Any,
                                p_up_independent: Any = None,
                                bid: Any, ask: Any,
                                exposure_headroom_usd: Any,
                                min_edge_usd: Any = None,
                                min_edge_prob: Any = None
                                ) -> Dict[str, Any]:
    """Is UNEQUAL inventory justified? Read-only. Refuses without a signal.

    `p_up_independent` is a probability of UP from a source INDEPENDENT of
    this book's price. None means we have none, which is the current state
    of the evidence, and the answer is then no -- not because unequal
    inventory is forbidden but because nothing distinguishes it from paying
    spread to repeat the market's own opinion.

    Returns `{"take": bool, "reason": str, ...}` with the balanced
    alternative always priced alongside, so the comparison is on the record.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, take=False, reason=reason,
                    market=(None if market is None else str(market)))

    nf, no_ = _d(n_favoured), _d(n_other)
    u, dn = _d(up_own_px), _d(down_own_px)
    b, a = _d(bid), _d(ask)
    room = _d(exposure_headroom_usd)
    floor = (_d(min_edge_usd) if min_edge_usd is not None
             else WEIGHT_MIN_EDGE_USD)
    pfloor = (_d(min_edge_prob) if min_edge_prob is not None
              else WEIGHT_MIN_EDGE_PROB)
    side = str(favoured_side).upper() if favoured_side is not None else None

    if nf is None or no_ is None or nf <= 0 or no_ <= 0:
        return no("WEIGHTS_UNREADABLE")
    if u is None or dn is None or u <= 0 or dn <= 0:
        return no("LEG_PRICES_UNREADABLE")
    if b is None or a is None or a <= b:
        return no("BOOK_NOT_TWO_SIDED")
    if room is None:
        return no("EXPOSURE_BUDGET_UNREADABLE")
    if side not in (TS.UP, TS.DOWN):
        return no("FAVOURED_SIDE_UNREADABLE")
    if nf == no_:
        return no("NOT_AN_UNEQUAL_ROUND")

    n_up, n_dn = ((nf, no_) if side == TS.UP else (no_, nf))
    spend = (n_up * u) + (n_dn * dn)
    # exact binary payouts, both outcomes
    if_up = (n_up * D(1)) - spend
    if_dn = (n_dn * D(1)) - spend
    worst, best = min(if_up, if_dn), max(if_up, if_dn)
    # the BALANCED alternative at the same prices, always on the record
    k = min(n_up, n_dn)
    bal = (k * (D(1) - (u + dn))) if k > 0 else D(0)
    mid = (b + a) / 2

    rec: Dict[str, Any] = {
        "market": (None if market is None else str(market)),
        "favoured_side": side, "n_up": str(n_up), "n_dn": str(n_dn),
        "up_own_px": str(u), "down_own_px": str(dn),
        "spend_usd": str(spend.quantize(D("0.0001"))),
        "if_up_wins_usd": str(if_up.quantize(D("0.0001"))),
        "if_down_wins_usd": str(if_dn.quantize(D("0.0001"))),
        "worst_case_usd": str(worst.quantize(D("0.0001"))),
        "best_case_usd": str(best.quantize(D("0.0001"))),
        "balanced_alternative_usd": str(bal.quantize(D("0.0001"))),
        "market_implied_p_up": str(mid),
        "exposure_headroom_usd": str(room),
        "unequal_inventory_is_not_risk_free": True,
        "labels": ("UNEQUAL INVENTORY, priced. An unequal round is a "
                   "directional bet carried on top of a pair: under a fair "
                   "market every leg earns only its own depth discount, so "
                   "weighting adds variance rather than expectancy. It is "
                   "admitted only on a probability estimate INDEPENDENT of "
                   "this book's price, because the mid is already the "
                   "market's own."),
    }

    # ---- capacity first, as everywhere else
    if room < -worst:
        return no("EXPOSURE_HEADROOM_BELOW_WORST_CASE", **rec)

    # ---- the signal, or its absence
    pi = _d(p_up_independent)
    if pi is None:
        return no("NO_INDEPENDENT_PROBABILITY_ESTIMATE", **rec)
    if not (D(0) < pi < D(1)):
        return no("INDEPENDENT_PROBABILITY_OUT_OF_RANGE", **rec)
    rec["p_up_independent"] = str(pi)
    edge_prob = pi - mid
    rec["probability_edge_vs_mid"] = str(edge_prob.quantize(D("0.0001")))
    # the estimate must favour the side we are overweighting, by enough
    want_up = side == TS.UP
    if (edge_prob > 0) != want_up:
        return no("ESTIMATE_DISAGREES_WITH_THE_OVERWEIGHTED_SIDE", **rec)
    if abs(edge_prob) < pfloor:
        return no("PROBABILITY_EDGE_BELOW_FLOOR",
                  min_edge_prob=str(pfloor), **rec)

    # ---- expectancy under the INDEPENDENT estimate, not the mid
    ev = (pi * if_up) + ((D(1) - pi) * if_dn)
    rec["expectancy_usd"] = str(ev.quantize(D("0.0001")))
    rec["expectancy_over_balanced_usd"] = str((ev - bal).quantize(D("0.0001")))
    rec["min_edge_usd"] = str(floor)
    if (ev - bal) <= floor:
        return no("NO_ADVANTAGE_OVER_THE_BALANCED_ROUND", **rec)
    return dict(rec, take=True, reason=None)


# ================================= ONE CONTROLLER OVER THE WHOLE ACTION SET
#
# Phase 1 asked for a single controller responsible for opening,
# accumulating, acquiring, completing, preserving, repricing, replenishing,
# reducing and holding -- and explicitly for NO competing controllers or
# duplicated decision logic.
#
# This is that entry point, and it is a DISPATCHER, not a third opinion. It
# owns one thing only: which FAMILY of actions the current inventory state
# puts us in. The pricing stays where it already lives and each is called at
# most once:
#
#     holding something  -> `decide`                  (6 disposition actions)
#     flat               -> `reopen_decision`         (OPEN / REPLENISH)
#                        -> `weighted_inventory_decision` (the unequal ones)
#
# Nothing is re-derived here. If a figure appears in the record it was
# returned by one of those three, and the pair ceilings, requote rules and
# risk bounds they consult are unchanged.


def decide_inventory(*, market: Any, held_side: Any = None, held_qty: Any = 0,
                     rounds_used: Any = 0,
                     disposition_kwargs: Optional[Mapping[str, Any]] = None,
                     reopen_kwargs: Optional[Mapping[str, Any]] = None,
                     weighting_kwargs: Optional[Mapping[str, Any]] = None
                     ) -> Dict[str, Any]:
    """The one controller. Dispatches by inventory state; prices nothing.

    Returns a record whose `selected` is a member of `UNIFIED_ACTIONS`, or
    None when no action is justified. `family` says which branch decided, so
    a reader can always tell what question was actually asked.

    WHY STATE AND NOT PREFERENCE DECIDES THE BRANCH. Holding an unmatched
    leg and being flat are not competing options between which expectancy
    can choose -- `EV(hold)` and `EV(complete)` are equal exactly, which is
    why `decide` ranks on the worst case instead. What differs is the
    QUESTION: with inventory the question is how to dispose of it, and flat
    the question is whether to take any on. Ranking those against each other
    would be comparing answers to different questions.

    FAILS CLOSED. An unreadable state takes no action at all, and the
    opening branch is refused rather than defaulted -- the safe direction
    for ADDING exposure is to stop.
    """
    rec: Dict[str, Any] = {
        "label": "L5_INVENTORY_DECISION",
        "market": (None if market is None else str(market)),
        "actions_available": list(UNIFIED_ACTIONS),
        "labels": ("ONE controller over the whole action set. This is a "
                   "dispatcher: it chooses the action FAMILY from inventory "
                   "state and defers every price to `decide`, "
                   "`reopen_decision` and `weighted_inventory_decision`, "
                   "each called at most once. No figure here is re-derived "
                   "and nothing here submits an order."),
    }
    hq = _d(held_qty)
    if hq is None:
        return dict(rec, family="UNREADABLE", selected=None,
                    selected_reason="HELD_QTY_UNREADABLE")

    # ---- HOLDING SOMETHING: dispose of it. `decide` owns this entirely.
    if hq > 0:
        if not disposition_kwargs:
            return dict(rec, family="DISPOSITION", selected=None,
                        selected_reason="DISPOSITION_INPUTS_MISSING")
        try:
            d = decide(market=market, held_side=held_side, held_qty=hq,
                       **dict(disposition_kwargs))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return dict(rec, family="DISPOSITION", selected=None,
                        selected_reason="DISPOSITION_RAISED",
                        detail=str(exc)[:160])
        # MERGED, NOT NESTED. `decide`'s record is the one the tape, the
        # heartbeat and `controller_preserves` already read, and a
        # dispatcher is no reason to change its shape. It gains the family
        # marker and keeps every key it had.
        return dict(d, inventory_family="DISPOSITION",
                    actions_available=list(UNIFIED_ACTIONS))

    # ---- FLAT: should we take exposure on at all, and in what shape?
    rec["family"] = "OPENING"
    if not reopen_kwargs:
        return dict(rec, selected=None,
                    selected_reason="OPENING_INPUTS_MISSING")
    try:
        r = reopen_decision(market=market, rounds_used=rounds_used,
                            **dict(reopen_kwargs))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return dict(rec, selected=None,
                    selected_reason="REOPEN_DECISION_RAISED",
                    detail=str(exc)[:160])
    rec["reopen"] = r
    if not r.get("reopen"):
        return dict(rec, selected=None,
                    selected_reason="REOPENING_NOT_JUSTIFIED:%s"
                                    % (r.get("reason") or "UNSTATED"))

    # the round is justified. BALANCED is the default shape and an unequal
    # one must beat it on the record -- never the other way round.
    try:
        used = int(rounds_used)
    except (TypeError, ValueError):
        used = 0
    base_action = OPEN if used <= 0 else REPLENISH

    if weighting_kwargs:
        try:
            w = weighted_inventory_decision(market=market,
                                            **dict(weighting_kwargs))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            w = {"take": False, "reason": "WEIGHTING_RAISED",
                 "detail": str(exc)[:160]}
        rec["weighting"] = w
        if w.get("take"):
            side = str(w.get("favoured_side") or "").upper()
            heavier_is_favoured = _d(w.get("n_up")) is not None and (
                (_d(w.get("n_up")) > _d(w.get("n_dn"))) == (side == TS.UP))
            return dict(rec,
                        selected=(ACCUMULATE_FAVORED if heavier_is_favoured
                                  else ACQUIRE_UNFAVORED),
                        selected_reason="UNEQUAL_ROUND_BEATS_BALANCED")

    return dict(rec, selected=base_action,
                selected_reason="BALANCED_ROUND_IS_JUSTIFIED")
