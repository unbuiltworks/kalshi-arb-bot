"""ONE decision process for unmatched inventory: compare, then act.

WHAT THIS REPLACES. Three separate things used to decide what to do about an
unmatched leg, each with its own economics and none of them aware of the
others:

  * `l5_two_sided_probe.complement_order` priced a MAKER complement against
    the 0.96 pair ceiling and said nothing about crossing;
  * `l5_two_sided_probe.completion_decision` priced a TAKER cross and asked
    only whether it improved the worse settlement outcome;
  * `l5_defensive_completion.decide` priced an unwind against a different
    ceiling ($1.20), a different budget and a different clock.

So the engine could hold a leg that one of them would have completed, be
refused by another, and carry the leg to settlement without any record of the
comparison. On 2026-11-06 the 0300z window produced 43 consecutive decisions
reading `ok: true, cross: true, worst_improvement_usd: 0.44-0.79` against
`displayed_depth: 1312.39` for a 1-contract need, and settled the leg for
-$0.62.

WHAT THIS IS. A pure ranking. Given the inventory, the current book and the
clock, it prices every executable alternative on the SAME arithmetic, says
which are unavailable and why, and names one. It submits nothing, reads
nothing, holds no transport and no store, and it cannot raise: a missing input
makes the alternative that needs it UNAVAILABLE with a named reason, never a
guessed number.

THE ALTERNATIVES, and why there are four and not five.

  HOLD            carry the residual to settlement. The baseline every other
                  alternative must STRICTLY beat on the worse outcome.
  MAKER_COMPLETE  rest a post-only quote on the deficient side.
  TAKER_COMPLETE  cross the displayed depth for the whole deficit.
  TAKER_PARTIAL   cross for less than the deficit, which is the right answer
                  when the full size does not clear the margin or the depth.

"REDUCE THE ORIGINAL EXPOSURE" IS NOT A FIFTH ALTERNATIVE ON THIS VENUE, and
saying so is more useful than inventing one. The book is quoted in YES terms
and a BUY of DOWN is wired as a SELL of YES, so selling the held UP leg into
the bid at `b` and buying the DOWN complement at own price `1 - b` are the
same order on the wire and the same arithmetic in the ledger:

    sell  1 UP at b          ->  net  b - pu
    buy   1 DOWN at (1 - b)  ->  net  1 - pu - (1 - b)  =  b - pu

`test_reducing_and_completing_are_the_same_order_on_this_venue` asserts the
identity rather than leaving it as a claim. So the complement BUY *is* the
exposure reduction, and what actually needed deciding -- how much of the
deficit to take, at what price, against what fill probability -- is what the
four alternatives above cover.

Selling a MATCHED pair back before settlement is also not offered: a matched
pair redeems $1.00 whichever way the market settles, and unwinding it pays
the spread to give that up. It can never rank first, so it is not priced.

HOW THEY ARE COMPARED, and WHY IT IS NOT BY EXPECTED VALUE.

Ranking by expected value looks obvious and is wrong here, structurally, in a
way worth spelling out because the first version of this module did it and
chose a maker quote with a 0.2% fill probability over a certain completion.

This programme has no opinion on outcomes -- that is its premise, not a
limitation. If it has no edge, the expected value of an unmatched leg is
whatever the market charged for it, which is to say about zero, and the
expected value of completing it is that same zero MINUS the completion fee.
So expected value prefers holding, always, for every unmatched leg, however
large the exposure. An EV ranking is therefore a machine for accumulating
directional risk, and accumulating directional risk is the thing this engine
is not authorized to do.

What the owner's mandate actually asks for -- "only authorize loss-reducing
taker when strictly better than carrying, under owner-specified maximum
completion-loss budget" -- is a different objective: remove worst-case
exposure, at a bounded price in expected value. So:

  1. Available. Every input the alternative needs was readable, the book is
     fresh, and the deployed cash and worst-case ceilings are satisfied.
     These are read, never computed here, so this cannot disagree with the
     gate that actually refuses.
  2. STRICTLY better worst case IF IT FILLS. `worst_after > worst_before`.
     This is what makes "do not blindly cross" mechanical rather than
     advisory, and HOLD wins by default when nothing clears it.
  3. The EV give-up against HOLD is within `max_ev_giveup_usd`. This is the
     completion-loss budget, and it is the gate that stops worst-case
     reduction being bought at any price. Unset means the constraint is not
     applied, and the record says so rather than implying a bound.
  4. Then, and only then, the highest EXPECTED WORST CASE:

         p_fill * worst_after + (1 - p_fill) * worst_before

     which is how much worst-case exposure the action really removes, given
     it might not fill. This is the number that correctly prefers a certain
     completion to a hopeful quote: a maker alternative that fills 0.2% of
     the time removes 0.2% of the exposure, and says so.
  5. Ties break toward less cash spent, then toward the higher fill
     probability.

Expected value is computed and reported for every alternative regardless, so
the trade-off being made is visible in the tape rather than implied.

ADVERSE SELECTION, WITH THE SIGN DERIVED RATHER THAN ASSUMED. We hold UP and
want DOWN. Buying DOWN as a maker means offering YES at `1 - d`, which fills
when the YES price FALLS -- that is, when UP is becoming less likely. So:

  * a FULL maker fill is outcome-independent ($1 either way) and adverse
    selection costs exactly nothing;
  * a PARTIAL fill leaves a residual UP in a world where UP just got less
    likely, which is where the cost lands;
  * a NON-fill means UP held up, which is favourable.

The model is a single shift, `adverse_shift`, applied to the settlement
probability CONDITIONAL on a fill, with the no-fill branch shifted the other
way by exactly the amount that preserves the unconditional prior:

    p_fill * p_given_fill + (1 - p_fill) * p_given_no_fill == p_prior

That constraint is what stops the shift being a free lunch in either
direction, and `test_the_adverse_shift_preserves_the_unconditional_prior`
asserts it. `adverse_shift` is an ASSUMPTION and is reported as one; it is
not measured here and nothing pretends it is.

FILL PROBABILITY IS SUPPLIED OR THE ALTERNATIVE IS UNAVAILABLE. A taker's is
derived from haircut displayed depth against the size wanted. A maker's needs
a measured base rate, and there is no default: `maker_fill_rate` None makes
MAKER_COMPLETE unavailable with MAKER_FILL_RATE_UNKNOWN rather than inventing
the one number that would make resting look good.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src import l5_two_sided_probe as TSP

HOLD = "HOLD"
MAKER_COMPLETE = "MAKER_COMPLETE"
TAKER_COMPLETE = "TAKER_COMPLETE"
TAKER_PARTIAL = "TAKER_PARTIAL"

ALTERNATIVES = (HOLD, MAKER_COMPLETE, TAKER_COMPLETE, TAKER_PARTIAL)

UP, DOWN = TSP.UP, TSP.DOWN

#: How far ahead a maker quote is given credit for being fillable. A BTC15m
#: window is 900 s and the complement is retired well before the terminal, so
#: a quote with less than this much runway is scaled down linearly.
MAKER_HORIZON_S = 300.0

#: The ranking key, named in every record so it is never inferred.
BASIS_EXPECTED_WORST = ("EXPECTED_WORSE_SETTLEMENT_OUTCOME"
                        "_AT_ESTIMATED_FILL_PROBABILITY")


def _d(v: Any) -> Optional[D]:
    return TSP._d(v)


def _rungs(raw: Any) -> Optional[Tuple[Tuple[D, D], ...]]:
    """[[own_price, size], ...] -> tuple, or None when unreadable.

    Unreadable is NOT empty. An empty book and a book we could not parse must
    refuse differently: the first is a fact about the market, the second is a
    fact about us.
    """
    if raw is None:
        return None
    out: List[Tuple[D, D]] = []
    try:
        for rung in raw:
            px, sz = _d(rung[0]), _d(rung[1])
            if px is None or sz is None or px <= 0 or px >= D(1) or sz < 0:
                return None
            out.append((px, sz))
    except (TypeError, IndexError, KeyError):
        return None
    return tuple(out)


def _floor_to(qty: D, inc: D) -> D:
    if inc is None or inc <= 0:
        return qty
    return (qty / inc).to_integral_value(rounding="ROUND_FLOOR") * inc


def walk_own_side(rungs: Sequence[Tuple[D, D]], want: D, *,
                  haircut: D) -> Dict[str, Any]:
    """VWAP and quantity a cross of `want` would really get, haircut applied.

    Cheapest own price first, because that is the order a buy consumes them
    in. Displayed size is discounted by `haircut` before it is believed: it is
    displayed, not guaranteed, and the whole point of crossing is that we are
    paying for certainty we have not got.
    """
    got, spend = D(0), D(0)
    levels = 0
    for px, sz in sorted(rungs, key=lambda r: r[0]):
        if got >= want:
            break
        usable = sz * haircut
        if usable <= 0:
            continue
        take = min(usable, want - got)
        got += take
        spend += take * px
        levels += 1
    if got <= 0:
        return {"ok": False, "reason": "NO_HAIRCUT_DEPTH", "qty": "0"}
    return {"ok": True, "qty": got, "vwap": spend / got, "levels": levels,
            "clears_in_full": got >= want}


def _settlement_probability(up_mid_wire: Any) -> Optional[D]:
    """The market's own implied probability that UP settles, or None.

    THE MID AND NOTHING ELSE. A model of our own here would be a prediction,
    and this programme is not a predictor -- its whole premise is that it has
    no opinion on outcomes. The venue's mid is the price at which the market
    is indifferent, so it is the only probability available that we are not
    making up. None propagates as None and the ranking says it fell back to
    the worse outcome alone.
    """
    p = _d(up_mid_wire)
    if p is None or p <= 0 or p >= D(1):
        return None
    return p


def adverse_conditioned(p_prior: D, p_fill: D, shift: D) -> Dict[str, D]:
    """Settlement probability conditional on a complement fill, and on none.

    The shift is applied against the HELD side and the no-fill branch is moved
    by exactly the amount that preserves the unconditional prior, so the model
    cannot create or destroy expected value -- it only moves it between the
    two branches, which is what adverse selection actually is.

    THE SHIFT IS REDUCED, NEVER CLAMPED AFTER THE FACT. At a high fill
    probability the balancing branch carries almost no weight, so a shift of
    0.05 would need the no-fill branch above 1.0 to stay balanced. Clamping
    the RESULT there silently breaks the identity and quietly invents expected
    value -- the first version did exactly that and
    `test_the_adverse_shift_preserves_the_unconditional_prior` caught it at
    p_fill 0.9. So the SHIFT is reduced to the largest value both branches can
    carry, the identity holds exactly at every fill probability, and the
    applied shift is returned so a reader can see it was tightened.

    A certain fill (p_fill == 1) has no no-fill branch to balance, so the
    conditional equals the prior: there is no selection when there is no
    selecting.
    """
    lo, hi = D("0.0001"), D("0.9999")
    if p_fill >= D(1) or p_fill <= D(0):
        return {"given_fill": p_prior, "given_no_fill": p_prior,
                "shift_applied": D(0)}
    feasible = min(shift, p_prior - lo, (D(1) - p_fill) * (hi - p_prior)
                   / p_fill)
    feasible = max(D(0), feasible)
    given_fill = p_prior - feasible
    # balance: p*gf + (1-p)*gnf == prior, exactly and by construction
    given_no_fill = (p_prior - p_fill * given_fill) / (D(1) - p_fill)
    return {"given_fill": given_fill, "given_no_fill": given_no_fill,
            "shift_applied": feasible}


def _pnl_pair(a: D, b: D, pu: D, pd: D, theta: D = TSP.TAKER_THETA
              ) -> Dict[str, D]:
    """Both settlement outcomes for a book, through the shared arithmetic."""
    out = TSP.net_settlement(up_qty=a, dn_qty=b, up_price=pu, dn_price=pd,
                             theta=theta)
    return {"up": out["up_pnl"], "dn": out["dn_pnl"],
            "worst": min(out["up_pnl"], out["dn_pnl"]),
            "cost": out["cost"]}


def _ev(p_up: Optional[D], up_pnl: D, dn_pnl: D) -> Optional[D]:
    if p_up is None:
        return None
    return p_up * up_pnl + (D(1) - p_up) * dn_pnl


def _unavailable(name: str, reason: str, **kw: Any) -> Dict[str, Any]:
    return dict({"alternative": name, "available": False, "reason": reason,
                 "worst_after": None, "worst_improvement_usd": None,
                 "expected_usd": None, "fill_probability": None,
                 "cash_outlay_usd": None, "qty": None, "own_px": None},
                **kw)


def taker_fill_probability(fillable: D, wanted: D) -> D:
    """How much of what we want the haircut book can actually serve.

    NOT 1.0 BECAUSE WE CROSSED. Crossing buys immediacy against DISPLAYED
    depth, and displayed depth is already discounted by the haircut before it
    reaches here. A cross for more than the haircut book holds is a partial
    fill with probability one of being partial, which is a different fact from
    a fill with probability less than one -- so this reports the FRACTION
    served and the partial alternative is priced at the size that clears.
    """
    if wanted <= 0:
        return D(0)
    return min(D(1), fillable / wanted)


def maker_fill_probability(*, base_rate: Optional[D], qty: D,
                           queue_ahead: Optional[D],
                           seconds_to_settlement: Optional[D],
                           expected_volume: Optional[D] = None,
                           horizon_s: D = D(str(MAKER_HORIZON_S))
                           ) -> Dict[str, Any]:
    """A measured base rate, scaled by runway and by the queue in front of us.

    THERE IS NO DEFAULT BASE RATE. `base_rate` None refuses, because the one
    number that decides whether resting beats crossing is exactly the number
    this module must not invent. Every scaling is bounded to [0, 1] and can
    only ever REDUCE the measured rate: less time cannot make a quote more
    likely to fill, and a longer queue cannot either.

    TWO QUEUE MODELS, AND THE RECORD NAMES WHICH ONE RAN.

      VOLUME  -- when `expected_volume` is supplied: a maker fills once the
                 size ahead of it plus its own has traded through, so the
                 probability is the share of that which the expected volume
                 covers. This is the model with a mechanism.
      RATIO   -- otherwise: 1 / (1 + queue_ahead / qty). A crude proxy with
                 no volume in it, and deliberately a harsh one. It errs
                 toward believing a resting quote will NOT fill, which is the
                 direction that prefers a certain completion to a hopeful
                 one -- and resting a quote that never filled while a
                 residual rode to settlement is the failure this module was
                 written after, not a hypothetical.
    """
    if base_rate is None:
        return {"ok": False, "reason": "MAKER_FILL_RATE_UNKNOWN"}
    if base_rate < 0 or base_rate > 1:
        return {"ok": False, "reason": "MAKER_FILL_RATE_OUT_OF_RANGE"}
    if seconds_to_settlement is None:
        return {"ok": False, "reason": "SETTLEMENT_TIME_UNKNOWN"}
    if queue_ahead is None:
        return {"ok": False, "reason": "QUEUE_AHEAD_UNKNOWN"}
    if qty <= 0:
        return {"ok": False, "reason": "QTY_NOT_POSITIVE"}
    runway = max(D(0), min(D(1), seconds_to_settlement / horizon_s))
    ahead = max(D(0), queue_ahead)
    if expected_volume is not None and expected_volume > 0:
        model = "VOLUME"
        queue = max(D(0), min(D(1), expected_volume / (ahead + qty)))
    else:
        model = "RATIO"
        queue = D(1) / (D(1) + (ahead / qty))
    return {"ok": True, "p": base_rate * runway * queue,
            "base_rate": base_rate, "runway_factor": runway,
            "queue_factor": queue, "queue_model": model,
            "basis": ("a MEASURED base rate scaled by remaining runway and "
                      "by the size ahead of us, under the named queue "
                      "model. Every factor is in [0,1] and can only "
                      "reduce it.")}


# =====================================================================
#                              the ranking
# =====================================================================

def rank(*, up_qty: Any, dn_qty: Any, up_cost_px: Any, dn_cost_px: Any,
         deficient_side: str, deficit: Any,
         min_qty: Any, tick: Any = None,
         taker_rungs: Any = None, taker_displayed_qty: Any = None,
         taker_own_px: Any = None,
         depth_haircut: Any = D("0.25"),
         maker_own_px: Any = None, maker_queue_ahead: Any = None,
         maker_fill_rate: Any = None,
         seconds_to_settlement: Any = None,
         up_mid_wire: Any = None,
         adverse_shift: Any = D("0.05"),
         expected_volume: Any = None,
         max_ev_giveup_usd: Any = None,
         cash_headroom: Any = None, worst_case_ceiling: Any = None,
         book_age_s: Any = None, max_book_age_s: Any = D("5"),
         gap_suspected: bool = False,
         partial_steps: int = 4,
         ) -> Dict[str, Any]:
    """Price every alternative for one market's unmatched inventory.

    Returns `{"ok", "chosen", "alternatives", ...}`. `chosen` is always one of
    the alternatives and is HOLD whenever nothing strictly improves the worse
    settlement outcome -- so a caller that acts on `chosen` can never cross
    without an improvement, and a caller that ignores it is the one taking a
    decision this module did not.
    """
    rec: Dict[str, Any] = {
        "process": "INVENTORY_AWARE_ALTERNATIVE_RANKING",
        "labels": ("ONE comparison over every executable alternative, on one "
                   "arithmetic. It prices nothing it was not given, submits "
                   "nothing, and chooses HOLD unless an alternative strictly "
                   "improves the worse settlement outcome."),
    }

    side = str(deficient_side or "").strip().upper()
    if side not in (UP, DOWN):
        return dict(rec, ok=False, reason="DEFICIENT_SIDE_UNKNOWN",
                    chosen=HOLD, alternatives=[])
    a, b = _d(up_qty), _d(dn_qty)
    pu, pd = _d(up_cost_px), _d(dn_cost_px)
    need = _d(deficit)
    inc = _d(min_qty)
    if a is None or b is None or pu is None or pd is None:
        return dict(rec, ok=False, reason="HELD_INVENTORY_UNREADABLE",
                    chosen=HOLD, alternatives=[])
    if need is None or need <= 0:
        return dict(rec, ok=False, reason="NO_DEFICIT_TO_COMPLETE",
                    chosen=HOLD, alternatives=[])
    if inc is None or inc <= 0:
        return dict(rec, ok=False, reason="QUANTITY_INCREMENT_UNKNOWN",
                    chosen=HOLD, alternatives=[])

    # ---- THE BOOK'S FRESHNESS IS A GATE, NOT A FIELD. A decision priced off
    # a stale or gapped book is a decision priced off a book that no longer
    # exists, and the expensive direction of this decision is acting.
    age, age_cap = _d(book_age_s), _d(max_book_age_s)
    stale: Optional[str] = None
    if gap_suspected:
        stale = "BOOK_GAP_SUSPECTED"
    elif age is None:
        stale = "BOOK_AGE_UNKNOWN"
    elif age_cap is not None and age > age_cap:
        stale = "BOOK_STALE:%s>%s" % (age, age_cap)

    haircut = _d(depth_haircut)
    if haircut is None or haircut <= 0 or haircut > 1:
        haircut = D("0.25")
    p_up = _settlement_probability(up_mid_wire)
    shift = _d(adverse_shift) or D(0)
    cash = _d(cash_headroom)
    ceiling = _d(worst_case_ceiling)
    secs = _d(seconds_to_settlement)

    base = _pnl_pair(a, b, pu, pd)
    worst_before = base["worst"]
    ev_before = _ev(p_up, base["up"], base["dn"])

    alts: List[Dict[str, Any]] = [{
        "alternative": HOLD, "available": True, "reason": "BASELINE",
        "qty": "0", "own_px": None, "fee_usd": "0", "cash_outlay_usd": "0",
        "fill_probability": "1",
        "worst_before": str(worst_before), "worst_after": str(worst_before),
        "worst_improvement_usd": "0",
        "expected_worst_after": str(worst_before),
        "expected_worst_improvement_usd": "0",
        "strictly_improves_worst": False,
        "expected_usd": None if ev_before is None else str(ev_before),
        "up_pnl_after": str(base["up"]), "dn_pnl_after": str(base["dn"]),
        "labels": ("carry the residual to settlement. Every other "
                   "alternative must strictly beat this on the worse "
                   "outcome to be chosen."),
    }]

    def _priced(name: str, qty: D, px: D, *, theta: D, p_fill: D,
                extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """One alternative, on the same arithmetic as every other."""
        if qty <= 0:
            return _unavailable(name, "QTY_BELOW_INCREMENT")
        tu = TSP.top_up(up_qty=a, dn_qty=b, up_price=pu, dn_price=pd,
                        extra_side=side, extra_qty=qty, extra_price=px)
        if not isinstance(tu, Mapping) or tu.get("worst_after") is None:
            return _unavailable(name, "TOP_UP_UNREADABLE")
        wa = _d(tu.get("worst_after"))
        if wa is None:
            return _unavailable(name, "TOP_UP_UNREADABLE")
        fee = TSP.fee(theta, qty, px)
        outlay = qty * px + max(D(0), fee)
        # WHAT THE FILLED AND UNFILLED BRANCHES ARE WORTH, separately.
        # A full fill is outcome-independent; the unfilled branch is the
        # inventory we started with, and adverse selection lives in the
        # difference between the two conditional distributions.
        after = _pnl_pair(*( (a + qty, b, (a * pu + qty * px) / (a + qty), pd)
                             if side == UP
                             else (a, b + qty, pu,
                                   (b * pd + qty * px) / (b + qty)) ),
                          theta=theta)
        cond = (adverse_conditioned(p_up, p_fill, shift)
                if p_up is not None else None)
        ev_fill = (None if cond is None
                   else _ev(cond["given_fill"], after["up"], after["dn"]))
        ev_nofill = (None if cond is None
                     else _ev(cond["given_no_fill"], base["up"], base["dn"]))
        ev = (None if ev_fill is None or ev_nofill is None
              else p_fill * ev_fill + (D(1) - p_fill) * ev_nofill)
        out = {
            "alternative": name, "available": True, "reason": "PRICED",
            "qty": str(qty), "own_px": str(px),
            "side": side,
            "fee_usd": str(fee), "cash_outlay_usd": str(outlay),
            "fill_probability": str(p_fill),
            "worst_before": str(worst_before), "worst_after": str(wa),
            "worst_improvement_usd": str(wa - worst_before),
            "strictly_improves_worst": bool(wa > worst_before),
            # HOW MUCH EXPOSURE THIS REALLY REMOVES, given it might not
            # fill. This is the ranking key: a quote that fills a fiftieth
            # of the time removes a fiftieth of the exposure.
            "expected_worst_after": str(
                p_fill * wa + (D(1) - p_fill) * worst_before),
            "expected_worst_improvement_usd": str(
                p_fill * (wa - worst_before)),
            "up_pnl_after": str(after["up"]), "dn_pnl_after": str(after["dn"]),
            "both_positive_after": bool(tu.get("both_positive_after")),
            "expected_usd": None if ev is None else str(ev),
            "expected_if_filled_usd": (None if ev_fill is None
                                       else str(ev_fill)),
            "expected_if_unfilled_usd": (None if ev_nofill is None
                                         else str(ev_nofill)),
            "adverse_selection": (None if cond is None else {
                "prior_up": str(p_up), "shift": str(shift),
                "shift_applied": str(cond["shift_applied"]),
                "p_up_given_fill": str(cond["given_fill"]),
                "p_up_given_no_fill": str(cond["given_no_fill"]),
                "basis": ("an ASSUMED shift against the held side, "
                          "conditional on a fill, balanced so the "
                          "unconditional prior is preserved. A FULL "
                          "completion is outcome-independent, so the cost "
                          "lands only on a partial fill or a non-fill.")}),
        }
        # ---- THE DEPLOYED LIMITS, READ NEVER COMPUTED, so this cannot
        # disagree with the gate that actually refuses.
        if cash is not None and outlay > cash:
            out.update(available=False, reason="CASH_HEADROOM_EXCEEDED",
                       cash_headroom=str(cash))
        elif ceiling is not None and wa < -ceiling:
            out.update(available=False, reason="WORST_CASE_CEILING_EXCEEDED",
                       worst_case_ceiling=str(ceiling))
        elif stale is not None:
            out.update(available=False, reason=stale)
        if extra:
            out.update(extra)
        return out

    # ------------------------------------------------ the taker alternatives
    rungs = _rungs(taker_rungs)
    disp = _d(taker_displayed_qty)
    tpx = _d(taker_own_px)
    if rungs:
        walk = walk_own_side(rungs, need, haircut=haircut)
        if not walk.get("ok"):
            alts.append(_unavailable(TAKER_COMPLETE, str(walk.get("reason"))))
            alts.append(_unavailable(TAKER_PARTIAL, str(walk.get("reason"))))
        else:
            full_q = _floor_to(min(need, walk["qty"]), inc)
            alts.append(_priced(
                TAKER_COMPLETE, full_q, walk["vwap"],
                theta=TSP.TAKER_THETA,
                p_fill=taker_fill_probability(walk["qty"], need),
                extra={"levels_used": walk["levels"],
                       "clears_in_full": walk["clears_in_full"],
                       "haircut": str(haircut),
                       "depth_basis": ("haircut DISPLAYED depth. Displayed "
                                       "is not guaranteed.")}))
            # PARTIAL IS NOT A CONSOLATION PRIZE. It is the right answer when
            # the full size does not clear or does not pay: a smaller cross
            # at a better VWAP can improve the worse outcome where the whole
            # deficit does not, and refusing to consider it is how an engine
            # ends up choosing between crossing badly and not crossing.
            best: Optional[Dict[str, Any]] = None
            # WHY NO PARTIAL SIZE WAS TAKEN, and it must be the real reason.
            # A blanket "no size improves" hid a cash-headroom refusal, which
            # is a different fact and a different fix.
            refusals: List[str] = []
            steps = max(1, int(partial_steps))
            for k in range(1, steps):
                want = _floor_to(need * D(k) / D(steps), inc)
                if want <= 0 or want >= full_q:
                    continue
                w = walk_own_side(rungs, want, haircut=haircut)
                if not w.get("ok"):
                    refusals.append(str(w.get("reason")))
                    continue
                cand = _priced(TAKER_PARTIAL, _floor_to(w["qty"], inc),
                               w["vwap"], theta=TSP.TAKER_THETA,
                               p_fill=taker_fill_probability(w["qty"], want),
                               extra={"levels_used": w["levels"],
                                      "fraction_of_deficit": str(
                                          D(k) / D(steps)),
                                      "haircut": str(haircut)})
                if not cand.get("available"):
                    refusals.append(str(cand.get("reason")))
                    continue
                if best is None or _d(cand["expected_worst_after"]) > _d(
                        best["expected_worst_after"]):
                    best = cand
            if best is not None:
                alts.append(best)
            else:
                alts.append(_unavailable(
                    TAKER_PARTIAL,
                    refusals[0] if refusals else "NO_PARTIAL_SIZE_AVAILABLE",
                    partial_refusals=refusals))
    elif tpx is not None and disp is not None:
        # NO RUNGS, ONE PRICE. The top of book alone, with the haircut on the
        # single displayed size. Reported as the degraded input it is.
        q = _floor_to(min(need, disp * haircut), inc)
        alts.append(_priced(TAKER_COMPLETE, q, tpx, theta=TSP.TAKER_THETA,
                            p_fill=taker_fill_probability(disp * haircut,
                                                          need),
                            extra={"depth_basis": "TOP_OF_BOOK_ONLY_NO_RUNGS",
                                   "haircut": str(haircut)}))
        alts.append(_unavailable(TAKER_PARTIAL, "NO_RUNGS_FOR_PARTIAL_WALK"))
    else:
        alts.append(_unavailable(TAKER_COMPLETE, "TAKER_BOOK_UNREADABLE"))
        alts.append(_unavailable(TAKER_PARTIAL, "TAKER_BOOK_UNREADABLE"))

    # ------------------------------------------------ the maker alternative
    mpx = _d(maker_own_px)
    if mpx is None:
        alts.append(_unavailable(MAKER_COMPLETE, "MAKER_PRICE_UNREADABLE"))
    else:
        mp = maker_fill_probability(
            base_rate=_d(maker_fill_rate), qty=need,
            queue_ahead=_d(maker_queue_ahead), seconds_to_settlement=secs,
            expected_volume=_d(expected_volume))
        if not mp.get("ok"):
            alts.append(_unavailable(MAKER_COMPLETE, str(mp.get("reason"))))
        else:
            alts.append(_priced(
                MAKER_COMPLETE, _floor_to(need, inc), mpx,
                theta=TSP.MAKER_THETA, p_fill=mp["p"],
                extra={"fill_model": {k: str(v) for k, v in mp.items()
                                      if k not in ("ok", "basis")},
                       "fill_model_basis": mp.get("basis"),
                       "fee_note": ("MAKER_THETA is negative: a maker "
                                    "completion expects a rebate rather "
                                    "than a fee, and `fee_usd` carries the "
                                    "sign. Only a CREDITED rebate is real; "
                                    "this is the venue's schedule, not a "
                                    "credit record.")}))

    # ---------------------------------------------------------- the choice
    #
    # THE COMPLETION-LOSS BUDGET, applied here and not inside `_priced`, so
    # that an alternative refused by it is still PRICED in the record. The
    # owner's question -- what did it cost in expected value to remove that
    # worst case -- cannot be answered from a row that was never computed.
    giveup_cap = _d(max_ev_giveup_usd)
    for x in alts:
        if x["alternative"] == HOLD or not x.get("available"):
            continue
        ev_alt, ev_h = _d(x.get("expected_usd")), ev_before
        if ev_alt is None or ev_h is None:
            x["ev_giveup_usd"] = None
            if giveup_cap is not None:
                # A BUDGET WE CANNOT MEASURE AGAINST IS NOT SATISFIED. With
                # no settlement probability there is no expected value, so a
                # configured budget refuses rather than being waived.
                x.update(available=False,
                         reason="EV_GIVEUP_UNMEASURABLE_NO_MID")
            continue
        x["ev_giveup_usd"] = str(ev_h - ev_alt)
        if giveup_cap is not None and (ev_h - ev_alt) > giveup_cap:
            x.update(available=False, reason="EV_GIVEUP_ABOVE_BUDGET",
                     max_ev_giveup_usd=str(giveup_cap))

    def _sortable(x: Dict[str, Any]) -> bool:
        return (bool(x.get("available"))
                and x.get("alternative") != HOLD
                and bool(x.get("strictly_improves_worst")))

    runners = [x for x in alts if _sortable(x)]

    def _key(x: Dict[str, Any]):
        return (
            _d(x.get("expected_worst_after")),
            -(_d(x.get("cash_outlay_usd")) or D(0)),
            _d(x.get("fill_probability")) or D(0),
        )

    chosen = max(runners, key=_key)["alternative"] if runners else HOLD
    return dict(
        rec, ok=True, chosen=chosen, alternatives=alts,
        ranking_basis=BASIS_EXPECTED_WORST,
        expected_value_role=(
            "a BOUND, not the objective. See this module's docstring: an EV "
            "ranking prefers carrying every unmatched leg, because a "
            "programme with no view on outcomes has EV zero for holding and "
            "EV minus-the-fee for completing."),
        max_ev_giveup_usd=None if giveup_cap is None else str(giveup_cap),
        ev_budget_applied=giveup_cap is not None,
        worst_before=str(worst_before),
        expected_hold_usd=None if ev_before is None else str(ev_before),
        deficient_side=side, deficit=str(need),
        settlement_probability_up=None if p_up is None else str(p_up),
        seconds_to_settlement=None if secs is None else str(secs),
        book_refused=stale,
        considered=[x["alternative"] for x in runners],
        why_hold=(None if runners else
                  ("no alternative both strictly improves the worse "
                   "settlement outcome and fits the completion-loss budget, "
                   "so the residual is carried under the existing controls")),
    )
