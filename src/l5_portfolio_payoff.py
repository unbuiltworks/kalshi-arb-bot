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

#: HEDGE BOUND -- and the first version of this was WRONG, which is worth
#: recording because the wrong version is the intuitive one.
#:
#: I first capped the upside surrendered per dollar of worst case repaired,
#: at 1.0. That rejected completing a 2.05 UP leg held at 0.465 with a DOWN
#: at 0.56: it surrenders $1.148 of best case to repair $0.902 of worst
#: case, a ratio of 1.27. But that completion locks a CERTAIN loss of only
#: $0.051, while carrying the same residual costs a MEASURED
#: -$0.2315/unit -- $0.475 on 2.05 units. The rule would have refused a
#: hedge nine times cheaper than the exposure it removes, which is exactly
#: the completion that would have saved the 0145z window's $0.953225.
#:
#: The defensible comparison is not best-case against worst-case. It is the
#: CERTAIN loss the hedge locks against the EXPECTED cost of carrying the
#: residual instead. Both are in dollars and both are measured.
#:
#: This also reproduces the known bad case: chasing a quote to own 0.95
#: against a 0.32 basis is a pair at 1.27, locking $0.55 on 2.05 units to
#: avoid $0.47 of expected carry -- refused, as it should be.
RESIDUAL_ADVERSE_USD_PER_UNIT = D("0.2315")

#: Where that figure comes from, carried so it can be audited.
RESIDUAL_ADVERSE_BASIS = ("measured residual adverse selection, 384 "
                          "windows, adverse_rate 0.89, swept_share 0.98")

#: Kept and REPORTED for audit, no longer a gate. See above for why.
MAX_HEDGE_COST_RATIO = D("1.0")

#: Fill fractions reported for the partial-fill scenarios required of any
#: maker order. A maker order is not a promise of its own size.
PARTIAL_FRACTIONS = (D("0.25"), D("0.50"), D("0.75"))

PROFIT_LOCK = "PROFIT_LOCK"
HEDGE = "HEDGE"
DIRECTIONAL_ADD = "DIRECTIONAL_ADD"
REJECT = "REJECT"


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


def evaluate(*, held_up: Any, held_dn: Any, cost_basis_usd: Any,
             side: Any, qty: Any, own_price: Any, fee_usd: Any,
             resting_same_side_qty: Any = 0,
             resting_same_side_cost_usd: Any = 0,
             marginal_ev_usd_per_contract: Any = None,
             marginal_ev_provenance: Any = None,
             risk_refusal: Any = None,
             max_hedge_cost_ratio: Any = None) -> Dict[str, Any]:
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
        given_up = -d_best if d_best < 0 else D(0)
        ratio = given_up / d_worst
        # WHAT THE HEDGE LOCKS, with certainty, if it fills.
        locked_loss = -full["worst_usd"] if full["worst_usd"] < 0 else D(0)
        # WHAT CARRYING THE RESIDUAL INSTEAD COSTS, in expectation, on the
        # unmatched units we hold BEFORE this order.
        unmatched_before = abs(before["up_qty"] - before["dn_qty"])
        carry = (unmatched_before * RESIDUAL_ADVERSE_USD_PER_UNIT
                 ).quantize(D("0.000001"))
        out = dict(base,
                   hedge_cost_ratio=str(ratio),
                   hedge_cost_ratio_note=("reported, NOT a gate -- see "
                                          "RESIDUAL_ADVERSE_USD_PER_UNIT"),
                   hedge_locks_certain_loss_usd=str(locked_loss),
                   residual_carry_expected_cost_usd=str(carry),
                   residual_carry_basis=RESIDUAL_ADVERSE_BASIS)
        if locked_loss > carry:
            return dict(out, action=REJECT,
                        reason="HEDGE_LOCKS_MORE_THAN_CARRY_COSTS",
                        labels=("the worst case improves, but this locks a "
                                "certain loss larger than the measured "
                                "expected cost of carrying the residual. "
                                "That is paying to make the outcome worse."))
        return dict(out, action=HEDGE, reason=None,
                    labels=("reduces the worst-case loss, and the certain "
                            "loss it locks is below the measured expected "
                            "cost of carrying the residual instead. "
                            "Protection begins at the FILL, never at the "
                            "submission."))

    # DIRECTIONAL_ADD: worsens the worst case. Needs an independently
    # justified marginal EV, with provenance. Absent evidence is refused.
    ev = _d(marginal_ev_usd_per_contract)
    prov = str(marginal_ev_provenance or "").strip()
    out = dict(base, action=REJECT)
    if ev is None:
        return dict(out, reason="DIRECTIONAL_EV_ABSENT",
                    labels=("this worsens the worst case and no marginal "
                            "expected value was supplied. Absent evidence "
                            "is not positive expected value."))
    if not prov:
        return dict(out, reason="DIRECTIONAL_EV_PROVENANCE_ABSENT",
                    marginal_ev_usd_per_contract=str(ev),
                    labels=("an expected value with no stated source cannot "
                            "be audited."))
    if ev <= 0:
        return dict(out, reason="DIRECTIONAL_EV_NOT_POSITIVE",
                    marginal_ev_usd_per_contract=str(ev),
                    marginal_ev_provenance=prov)
    return dict(base, action=DIRECTIONAL_ADD, reason=None,
                marginal_ev_usd_per_contract=str(ev),
                marginal_ev_provenance=prov,
                labels=("increases favoured-side exposure on an "
                        "independently justified marginal expected value. "
                        "The worst case is WORSE after this fills."))
