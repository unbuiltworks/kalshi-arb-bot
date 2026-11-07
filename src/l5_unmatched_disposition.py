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
