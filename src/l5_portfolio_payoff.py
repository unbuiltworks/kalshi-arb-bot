"""Both settlement outcomes, priced before and after a proposed order.

WHY THIS CAN BE EXACT. A BTC15m market has exactly two outcomes and a
matched UP+DOWN pair redeems exactly $1.00. So the portfolio's value in each
outcome is arithmetic, not a model: if UP settles, every UP contract pays
$1.00 and every DOWN contract pays nothing, and vice versa. Nothing here
estimates a probability, and nothing here needs one -- the classification
depends only on the two payoffs and what the order costs.

WHAT THE SETTLED CAMPAIGN SHOWED THIS IS FOR. 2026-11-02, four windows:
matched pairs returned +$0.270564 while the unmatched remainder swung
-$0.953225 to +$1.191300 per window. The engine had no single place that
asked "what do I own in each outcome, and what would this order make that?"
-- so it could not tell a completion that locks a profit from an addition
that buys a lottery ticket. This is that place.

=========================================================================
AN UNFILLED ORDER IS NOT PROTECTION. THIS IS THE RULE THE MODULE EXISTS
TO ENFORCE AND THE EASIEST ONE TO BREAK.
=========================================================================

A resting hedge is an intention. Until the venue confirms a fill it changes
no payoff, and a worst case computed as though it had filled is a worst case
that understates the loss. So:

  * `before` counts HELD inventory only. Outstanding orders of ours are NOT
    credited as protection.
  * `after` is explicitly the IF-IT-FILLS case, and every record says so.
  * `unfilled` is reported as its own scenario, identical to `before`, so a
    reader can never mistake the two.
  * risk-INCREASING outstanding orders on our own side ARE charged in the
    worst case, because those do fill when the market runs against us.
    Protection is discounted, exposure is not -- the same asymmetry
    `residual_cap_decision` uses, and for the same reason.

FEE-INCLUSIVE COST BASIS, ALWAYS. Cost basis is `usdcSize` as the venue
reports it, never price x size (see analysis/l5/fees/FEE_RECONSTRUCTION.md).
This module takes the basis it is given and does not reconstruct it; a
missing basis is UNKNOWN and refuses rather than defaulting to zero, because
a zero basis makes every position look profitable.
"""
from __future__ import annotations

import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Optional

#: Off by default and behind its own variable, so its effect is measurable
#: apart from the event-driven wakeups, the requote queue gate and the
#: inventory-preference rule.
ENV = "L5_PORTFOLIO_PAYOFF"

UP, DOWN = "UP", "DOWN"

#: A matched pair redeems exactly this. Not a parameter.
REDEMPTION = D("1.00")

#: ============================================================= TWO REMOVALS
#:
#: THIS MODULE HAS NO CONDITIONAL EV, AND MUST NOT ACQUIRE ONE BY ACCIDENT.
#: Two earlier versions of the hedge bound were both wrong, and both wrong
#: in the same way -- a number that looked like economics standing in for an
#: expectation nobody had measured.
#:
#: FIRST WRONG VERSION: a ratio capping upside surrendered per dollar of
#: worst case repaired. It refused completing a 2.05 UP leg held at 0.465
#: with a DOWN at 0.56 (ratio 1.27) even though that locks a CERTAIN $0.051
#: -- the very completion that would have saved 0145z's $0.953225. A ratio
#: is a derived quantity wearing the costume of an expected value.
#:
#: SECOND WRONG VERSION: comparing the locked loss against
#: -$0.2315/unit. That figure is an UNCONDITIONAL average over 384 windows
#: of whatever residuals we happened to hold. Using it as the conditional
#: expectation of a SPECIFIC position is wrong three ways: it embeds the
#: entry behaviour that produced it (resting deep, adverse_rate 0.89), the
#: maximum loss of a residual is its own basis and that varies enormously
#: (2.05 at own 0.465 risks $0.953; at own 0.90 it risks $1.845), and the
#: calibration study established we have NO reliable conditional settlement
#: estimate at all (base rate 0.5135, no bucket positive at 95%, CI
#: half-widths +/-0.20..0.45 against a required 0.022).
#:
#: SO THE GATE USES NEITHER. Eligibility is: the order reduces unmatched
#: inventory, the worst case improves after fees, and the CERTAIN locked
#: loss fits inside OWNER-DEFINED budgets. Every one of those is either
#: arithmetic or a policy number the owner set. No probability appears.
RESIDUAL_ADVERSE_USD_PER_UNIT = D("0.2315")

#: REPORTED FOR REFERENCE ONLY and consumed by nothing. Kept so a reader can
#: see the figure and its provenance, and see that it is unconditional.
RESIDUAL_ADVERSE_BASIS = ("UNCONDITIONAL average over 384 windows "
                          "(adverse_rate 0.89, swept_share 0.98). NOT a "
                          "conditional expectation for any specific "
                          "position, and NOT used by any gate here.")

#: RECOMMENDED FIGURES, AND NOT DEFAULTS. Nothing falls back to these.
#:
#: A cap the owner did not approve is not a limit, so a DEFENSIVE_HEDGE with
#: either budget missing is REFUSED rather than quietly admitted at some
#: number this module chose. These two values exist only to be quoted in a
#: proposal; `evaluate` never substitutes them.
RECOMMENDED_LOCKED_LOSS_PER_HEDGE_USD = D("0.15")
RECOMMENDED_CUMULATIVE_LOCKED_LOSS_USD = D("0.60")

#: Fill fractions reported for the partial-fill scenarios required of any
#: maker order. A maker order is not a promise of its own size.
PARTIAL_FRACTIONS = (D("0.25"), D("0.50"), D("0.75"))

PROFIT_LOCK = "PROFIT_LOCK"
DEFENSIVE_HEDGE = "DEFENSIVE_HEDGE"
#: INTENTIONAL ACCUMULATION ON EITHER SIDE. It introduces NO signal of its
#: own and defers entirely to the existing entry strategy and the hard
#: limits. The name matters: it is not a directional strategy.
INVENTORY_ADD = "INVENTORY_ADD"
REJECT = "REJECT"

#: Enabling INVENTORY_ADD needs FOUR independent things, and a feature flag
#: is only one of them. A flag that could unlock accumulation by itself is
#: precisely the defect this guards against:
#:   1. L5_PORTFOLIO_PAYOFF_INVENTORY_ADD set, AND
#:   2. a marginal EV figure, AND
#:   3. its provenance, AND
#:   4. owner authorization passed in by the caller from the real
#:      authorization layer -- never synthesised here.
INVENTORY_ADD_ENV = "L5_PORTFOLIO_PAYOFF_INVENTORY_ADD"


def inventory_add_flag() -> bool:
    """The FLAG only. Necessary, never sufficient -- see INVENTORY_ADD."""
    return (os.getenv(INVENTORY_ADD_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


def enabled() -> bool:
    return (os.getenv(ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def settlement_payoff(up_qty: Any, dn_qty: Any,
                      cost_basis_usd: Any) -> Optional[Dict[str, Any]]:
    """What the book is worth in each of the two outcomes.

    `cost_basis_usd` is the fee-inclusive total already paid for everything
    held. None anywhere returns None -- an unknown basis must not become a
    zero basis, which would make every position look profitable.
    """
    u, d_, c = _d(up_qty), _d(dn_qty), _d(cost_basis_usd)
    if u is None or d_ is None or c is None:
        return None
    if u < 0 or d_ < 0:
        return None
    pnl_up = (u * REDEMPTION) - c
    pnl_dn = (d_ * REDEMPTION) - c
    return {"pnl_if_up_usd": pnl_up, "pnl_if_down_usd": pnl_dn,
            "worst_usd": min(pnl_up, pnl_dn),
            "best_usd": max(pnl_up, pnl_dn),
            "up_qty": u, "dn_qty": d_, "cost_basis_usd": c}


def _fmt(p: Dict[str, Any]) -> Dict[str, str]:
    return {k: str(v) for k, v in p.items()}


#: THE MARKET-IMPLIED SETTLEMENT PROBABILITY, and the reason it is the only
#: calibrated one we will use.
#:
#: The owner refused the historical -$0.2315/unit residual average as a
#: conditional expectation, correctly: it was an UNCONDITIONAL mean over 384
#: windows and the calibration study found no bucket positive at 95% with CI
#: half-widths of 0.20-0.45 against a required 0.022. That gap has not
#: closed and nothing here reopens it.
#:
#: What IS available is contemporaneous and position-specific: on a market
#: where a matched pair redeems exactly $1.00, the complement's own
#: executable price IS the market's probability that the complement wins.
#: It is observed at the moment of the decision, it is the price we would
#: actually pay, and it needs no model.
#:
#: ITS ONE ASSUMPTION, stated rather than buried: that the venue's price is
#: an unbiased forecast. If it is biased we inherit the bias. That is why
#: the verdict below is labelled MARKET_IMPLIED and never TRUE_EV, and why a
#: hedge it rejects can still be taken under an explicit owner override.
#:
#: THE ARITHMETIC CONSEQUENCE, which is the whole finding. A hedge bought AT
#: the market costs the market's own expectation plus the spread and the
#: fee. So EV(hedge) <= EV(hold) ALWAYS, with equality only at zero spread
#: and zero fee. A defensive hedge priced at market can therefore never be
#: EV-improving; it buys variance reduction and pays the spread for it.
#: Measured on the 2026-11-02 campaign's three stranded legs, HOLD was
#: EV-superior in 3 of 3 by $0.0728-$0.1177, and hedging all three would
#: have taken the campaign from -$0.2356 to -$0.7311.
MARKET_IMPLIED = "MARKET_IMPLIED"
EV_IMPROVING = "EV_IMPROVING"
EV_NEGATIVE_RISK_REDUCING = "EV_NEGATIVE_RISK_REDUCING"


def market_implied_hold_ev(*, held_qty: Any, cost_basis_usd: Any,
                           complement_own_price: Any) -> Dict[str, Any]:
    """EV of HOLDING an unmatched position, priced by the complement.

    `complement_own_price` is P(the complement wins) = P(we lose). None,
    out of (0, 1), or an unreadable quantity leaves the verdict UNKNOWN --
    it is never guessed and never defaulted.
    """
    q = _d(held_qty)
    c = _d(cost_basis_usd)
    p = _d(complement_own_price)
    if q is None or c is None or p is None or q <= 0 or not (0 < p < 1):
        return {"status": "UNKNOWN", "ev_usd": None,
                "p_complement_wins": None if p is None else str(p),
                "basis": ("no executable complement price, so no calibrated "
                          "settlement probability exists for this position")}
    ev = (D(1) - p) * (q - c) + p * (-c)
    return {"status": MARKET_IMPLIED, "ev_usd": str(ev),
            "p_complement_wins": str(p),
            "basis": ("the complement's own executable price is the "
                      "market's probability that the complement wins. "
                      "Assumes the venue price is an unbiased forecast; "
                      "biased, we inherit the bias.")}


def evaluate(*, held_up: Any, held_dn: Any, cost_basis_usd: Any,
             side: Any, qty: Any, own_price: Any, fee_usd: Any,
             resting_same_side_qty: Any = 0,
             resting_same_side_cost_usd: Any = 0,
             marginal_ev_usd_per_contract: Any = None,
             marginal_ev_provenance: Any = None,
             owner_authorized_inventory_add: Any = False,
             risk_refusal: Any = None,
             max_locked_loss_per_hedge_usd: Any = None,
             # NO DEFAULT. It must come from the DURABLE ledger; a
             # committed amount nobody supplied is unknown, not zero.
             cumulative_locked_loss_usd: Any = None,
             max_cumulative_locked_loss_usd: Any = None,
             complement_own_price: Any = None,
             permit_ev_negative_hedge: Any = False,
             partial_risk_refusals: Any = None) -> Dict[str, Any]:
    """Classify a proposed order by what it does to BOTH settlement outcomes.

    `own_price` is the EXECUTABLE own cost of the side being bought -- the
    ask for UP, or 1-bid for DOWN. A midpoint is not a price anyone can
    transact at and must not be passed here.

    `fee_usd` is the fee for this order as the venue schedule gives it; 0 for
    a maker fill under the published schedule. None is UNKNOWN and rejects.

    `risk_refusal`, when set, is an existing control that already said no --
    the residual cap, the worst-case deficit bound, the exposure ceiling,
    the cash cap or an authorization gate. It OUTRANKS every economic
    verdict here. This module grades economics; it never overrides a limit.
    """
    if risk_refusal:
        return {"action": REJECT, "reason": "RISK_CONTROL_REFUSED",
                "risk_refusal": str(risk_refusal)[:120],
                "labels": ("an existing risk control refused this order. No "
                           "payoff arithmetic can overrule it.")}

    s = str(side).upper()
    if s not in (UP, DOWN):
        return {"action": REJECT, "reason": "SIDE_UNKNOWN", "side": str(side)}
    q, px, fee = _d(qty), _d(own_price), _d(fee_usd)
    if q is None or q <= 0:
        return {"action": REJECT, "reason": "QTY_UNREADABLE", "qty": str(qty)}
    if px is None or px <= 0 or px >= 1:
        return {"action": REJECT, "reason": "OWN_PRICE_OUT_OF_BAND",
                "own_price": str(own_price)}
    if fee is None or fee < 0:
        return {"action": REJECT, "reason": "FEE_UNKNOWN",
                "fee_usd": str(fee_usd),
                "labels": ("an unknown fee is not a zero fee; a zero "
                           "default would overstate every payoff.")}

    before = settlement_payoff(held_up, held_dn, cost_basis_usd)
    if before is None:
        return {"action": REJECT, "reason": "PORTFOLIO_UNREADABLE",
                "labels": ("held inventory or its fee-inclusive cost basis "
                           "could not be read. An unknown basis must never "
                           "be treated as zero.")}

    # EXPOSURE WE ALREADY CARRY ON THIS SIDE IS CHARGED, PROTECTION IS NOT.
    # Still-working orders on the side we are about to add to do fill when
    # the market runs against us, so the worst case must include them.
    rq = _d(resting_same_side_qty) or D(0)
    rc = _d(resting_same_side_cost_usd) or D(0)
    if rq < 0 or rc < 0:
        return {"action": REJECT, "reason": "RESTING_UNREADABLE"}

    def _after(fill_q: D) -> Optional[Dict[str, Any]]:
        add_cost = (fill_q * px) + (fee if fill_q == q else
                                    (fee * fill_q / q))
        u = before["up_qty"] + (fill_q if s == UP else D(0))
        d2 = before["dn_qty"] + (D(0) if s == UP else fill_q)
        # same-side still-working exposure, charged
        u += rq if s == UP else D(0)
        d2 += D(0) if s == UP else rq
        return settlement_payoff(u, d2,
                                 before["cost_basis_usd"] + add_cost + rc)

    full = _after(q)
    if full is None:
        return {"action": REJECT, "reason": "PAYOFF_UNCOMPUTABLE"}

    marginal_cost = full["cost_basis_usd"] - before["cost_basis_usd"]
    d_worst = full["worst_usd"] - before["worst_usd"]
    d_best = full["best_usd"] - before["best_usd"]

    scenarios = {
        # IDENTICAL TO `before`, and named so nobody reads a resting order
        # as protection.
        "unfilled": _fmt(before),
        "full_fill": _fmt(full),
    }
    for f in PARTIAL_FRACTIONS:
        pf = _after((q * f).quantize(D("0.0001")))
        if pf is not None:
            scenarios["partial_%s" % str(f)] = _fmt(pf)

    base: Dict[str, Any] = {
        "side": s, "qty": str(q), "own_price": str(px), "fee_usd": str(fee),
        "before": _fmt(before), "after_if_filled": _fmt(full),
        "marginal_cost_usd": str(marginal_cost),
        "delta_worst_usd": str(d_worst), "delta_best_usd": str(d_best),
        "scenarios": scenarios,
        "fill_uncertainty": ("a maker order is not a promise of its own "
                             "size. `after_if_filled` is CONDITIONAL; until "
                             "the venue confirms a fill the live payoff is "
                             "`unfilled`, which equals `before`."),
        "resting_same_side_charged_qty": str(rq),
    }

    # PROFIT_LOCK: both outcomes positive once this fills. For a completion
    # this is exactly pair cost < redemption, and it needs no probability.
    if full["pnl_if_up_usd"] > 0 and full["pnl_if_down_usd"] > 0:
        return dict(base, action=PROFIT_LOCK, reason=None,
                    labels=("both settlement outcomes are positive after "
                            "this fills. Still conditional on the fill."))

    # HEDGE: repairs the worst case, and pays no more upside than the
    # downside it removes.
    if d_worst > 0:
        # ELIGIBILITY, AND NOT ONE PROBABILITY IN IT.
        #   (1) it reduces unmatched inventory -- not to zero, just less.
        #       An imbalance left standing is fine and often intended.
        #   (2) the worst case improves after fees, at the INTENDED qty.
        #   (3) the CERTAIN locked loss fits the owner's per-hedge and
        #       cumulative budgets.
        unmatched_before = abs(before["up_qty"] - before["dn_qty"])
        unmatched_after = abs(full["up_qty"] - full["dn_qty"])
        locked_loss = -full["worst_usd"] if full["worst_usd"] < 0 else D(0)
        per_cap = _d(max_locked_loss_per_hedge_usd)
        cum_cap = _d(max_cumulative_locked_loss_usd)
        spent = _d(cumulative_locked_loss_usd)
        # NO SILENT FALLBACK, for any of the three. A missing cap is not a
        # generous cap, and an unknown cumulative spend is not a spend of
        # zero -- that is precisely the defect this replaces, where the
        # wired gate passed nothing and a $0.60 cumulative budget
        # degenerated into a second per-hedge cap.
        missing = [n for n, v in (("max_locked_loss_per_hedge_usd", per_cap),
                                  ("max_cumulative_locked_loss_usd", cum_cap),
                                  ("cumulative_locked_loss_usd", spent))
                   if v is None or v < 0]
        if missing:
            return dict(base, action=REJECT,
                        reason="HEDGE_BUDGET_NOT_SUPPLIED",
                        missing_budget_inputs=missing,
                        labels=("a defensive hedge needs the owner-approved "
                                "per-hedge cap, cumulative cap AND the "
                                "durable committed amount. A missing cap is "
                                "not a permissive one."))

        out = dict(base,
                   unmatched_before=str(unmatched_before),
                   unmatched_after_if_filled=str(unmatched_after),
                   reduces_unmatched=bool(unmatched_after < unmatched_before),
                   hedge_locks_certain_loss_usd=str(locked_loss),
                   max_locked_loss_per_hedge_usd=str(per_cap),
                   cumulative_locked_loss_usd=str(spent),
                   max_cumulative_locked_loss_usd=str(cum_cap),
                   # EXACT BRACKETS FOR THIS SPECIFIC POSITION, arithmetic.
                   holding_worst_usd=str(before["worst_usd"]),
                   holding_best_usd=str(before["best_usd"]),
                   unconditional_reference_only=RESIDUAL_ADVERSE_BASIS)

        # ============ HEDGE vs HOLD, PRICED BY THE MARKET ITSELF.
        #
        # The owner's rule: a hedge must not be taken MERELY because it
        # reduces the worst case. So the two must be separable, and they
        # are -- using the complement's own executable price as P(it wins).
        # No model, no historical average, contemporaneous, and it is the
        # price we would actually pay.
        _sd = str(side or "").upper()
        # DEFAULTS TO `own_price`, which for a hedge IS the complement's own
        # executable cost -- the same number, so no caller changes.
        #
        # AND IT IS THE HEDGE-FAVOURABLE CHOICE, deliberately. `own_price`
        # is the ASK we would pay; the market's true P(complement wins) sits
        # BELOW it by the half-spread. Using the ask therefore overstates
        # the chance we lose, which understates EV(HOLD) and makes hedging
        # look BETTER than it is. The conclusion below survives that bias,
        # which is why it is left in rather than corrected out.
        _p = _d(complement_own_price)
        if _p is None:
            _p = _d(own_price)
        _ev_hold = _ev_hedge = None
        _ev_status = "UNKNOWN"
        if _p is not None and D(0) < _p < D(1) and _sd in ("UP", "DOWN"):
            if _sd == "UP":
                _ev_hold = (_p * before["pnl_if_up_usd"]
                            + (D(1) - _p) * before["pnl_if_down_usd"])
                _ev_hedge = (_p * full["pnl_if_up_usd"]
                             + (D(1) - _p) * full["pnl_if_down_usd"])
            else:
                _ev_hold = (_p * before["pnl_if_down_usd"]
                            + (D(1) - _p) * before["pnl_if_up_usd"])
                _ev_hedge = (_p * full["pnl_if_down_usd"]
                             + (D(1) - _p) * full["pnl_if_up_usd"])
            _ev_status = MARKET_IMPLIED
        _ev_delta = (None if _ev_hold is None
                     else _ev_hedge - _ev_hold)
        _verdict = ("UNKNOWN" if _ev_delta is None
                    else (EV_IMPROVING if _ev_delta > 0
                          else EV_NEGATIVE_RISK_REDUCING))
        out = dict(out,
                   holding_ev_status=_ev_status,
                   holding_ev_usd=None if _ev_hold is None else str(_ev_hold),
                   hedge_ev_usd=None if _ev_hedge is None else str(_ev_hedge),
                   hedge_ev_delta_usd=(None if _ev_delta is None
                                       else str(_ev_delta)),
                   hedge_ev_verdict=_verdict,
                   p_complement_wins=None if _p is None else str(_p),
                   holding_ev_note=(
                       "EV of HOLD and of HEDGE under the complement's own "
                       "executable price. A hedge bought AT the market pays "
                       "the market's expectation plus the spread and fee, "
                       "so it is EV-negative by construction; it buys "
                       "variance reduction. UNKNOWN when no executable "
                       "complement price was supplied."))

        if unmatched_after >= unmatched_before:
            return dict(out, action=REJECT,
                        reason="DOES_NOT_REDUCE_UNMATCHED",
                        labels=("a defensive hedge must leave less unmatched "
                                "inventory than it found. It need not reach "
                                "balance."))
        if locked_loss > per_cap:
            return dict(out, action=REJECT,
                        reason="LOCKED_LOSS_EXCEEDS_PER_HEDGE_BUDGET")
        if (spent + locked_loss) > cum_cap:
            return dict(out, action=REJECT,
                        reason="LOCKED_LOSS_EXCEEDS_CUMULATIVE_BUDGET",
                        cumulative_after_usd=str(spent + locked_loss))
        # HARD RISK CONSTRAINTS AT EVERY FEASIBLE PARTIAL. A partial need
        # NOT improve the worst case -- requiring that would refuse sound
        # hedges, since the DOWN-win branch gains q(1-p) while the UP-win
        # branch loses q*p and past a certain size the minimum stops
        # improving. What a partial MUST do is respect every hard limit.
        refusals = dict(partial_risk_refusals or {})
        if refusals:
            return dict(out, action=REJECT,
                        reason="PARTIAL_FILL_BREACHES_RISK_LIMIT",
                        partial_risk_refusals=refusals)
        # THE OWNER'S RULE, ENFORCED: not merely because it reduces the
        # worst case. Measured on the 2026-11-02 campaign, HOLD was
        # EV-superior on 3 of 3 stranded legs and hedging all three would
        # have taken the campaign from -$0.2356 to -$0.7311. An EV-negative
        # hedge is still AVAILABLE -- it is variance reduction and may be
        # worth buying -- but it takes an explicit owner decision, never a
        # default.
        if _verdict == EV_NEGATIVE_RISK_REDUCING and not bool(
                permit_ev_negative_hedge):
            return dict(out, action=REJECT,
                        reason="HEDGE_EV_NEGATIVE_VS_HOLD",
                        labels=("this hedge reduces the worst case but is "
                                "EV-NEGATIVE against holding at the "
                                "market's own price. Risk reduction is not "
                                "a reason to trade on its own; taking it "
                                "anyway needs permit_ev_negative_hedge."))
        if _verdict == "UNKNOWN":
            return dict(out, action=REJECT,
                        reason="HEDGE_EV_UNRESOLVED",
                        labels=("no executable complement price was "
                                "supplied, so HEDGE could not be compared "
                                "with HOLD. Unresolved is not permission."))
        return dict(out, action=DEFENSIVE_HEDGE, reason=None,
                    labels=("reduces unmatched inventory, improves the "
                            "worst case after fees, sits inside the owner's "
                            "locked-loss budgets AND is EV-improving "
                            "against holding at the market's own price. "
                            "Protection begins at the FILL, never at the "
                            "submission."))

    # INVENTORY_ADD: intentional accumulation. Worsens the worst case, so
    # it needs FOUR independent things and a flag is only one of them.
    ev = _d(marginal_ev_usd_per_contract)
    prov = str(marginal_ev_provenance or "").strip()
    out = dict(base, action=REJECT,
               inventory_add_flag=bool(inventory_add_flag()),
               owner_authorized_inventory_add=bool(
                   owner_authorized_inventory_add))
    if not inventory_add_flag():
        return dict(out, reason="INVENTORY_ADD_DISABLED",
                    labels=("intentional accumulation is architecturally "
                            "available and disabled by default. The flag is "
                            "necessary and NEVER sufficient."))
    if not owner_authorized_inventory_add:
        return dict(out, reason="INVENTORY_ADD_NOT_OWNER_AUTHORIZED",
                    labels=("the flag is set but no owner authorization was "
                            "supplied by the caller. A feature flag must "
                            "never bypass owner authorization."))
    if ev is None:
        return dict(out, reason="MARGINAL_EV_ABSENT",
                    labels=("absent evidence is not positive expected "
                            "value."))
    if not prov:
        return dict(out, reason="MARGINAL_EV_PROVENANCE_ABSENT")
    if ev <= 0:
        return dict(out, reason="MARGINAL_EV_NOT_POSITIVE",
                    marginal_ev_usd_per_contract=str(ev))
    return dict(base, action=INVENTORY_ADD, reason=None,
                marginal_ev_usd_per_contract=str(ev),
                marginal_ev_provenance=prov,
                inventory_add_flag=True,
                owner_authorized_inventory_add=True,
                labels=("intentional accumulation on a validated marginal "
                        "expected value WITH owner authorization. The worst "
                        "case is WORSE after this fills."))
