"""Are we modelling L5's order lifecycle too literally from his fills?

THE HYPOTHESIS. Polymarket's public activity tells us when L5 FILLED, not when
he PLACED. If he keeps eligible quotes resting on BOTH outcomes and simply
waits, then his two fills are two arrivals against standing orders. A clone
that instead does

    first fill -> observe L5's next fill -> infer complement -> place second
    quote

is not running his policy at all: it chases each leg AFTER the flow that would
have filled it has already gone. That would produce naked residuals from a
perfectly reasonable behavioural model -- which is exactly what we observe
(7 positions, 0 paired, -$102.54 of pure directional residual).

TWO ARMS, ONE FROZEN TRIGGER.

    R  REACTIVE          today's clone. One quote, on the side L5 just filled.
    S  STATE-DRIVEN      the contemporaneous state implies desired inventory on
                         BOTH outcomes, so both quotes may rest before either
                         fills.

Arm S is a MODEL CANDIDATE and is labelled as one everywhere it is recorded. It
is NOT a claim about what L5 does. It is the falsifiable form of the
continuous-inventory hypothesis this programme has been developing, and the
comparison is what makes it refutable: if S forms no more pairs than R, the
hypothesis is wrong and we will have the number that says so.

WHAT ARM S IS NOT ALLOWED TO DO, because each would buy pairs with the edge we
are trying to keep:

  * it never widens a price. Each leg still joins the bid.
  * it never crosses the spread.
  * it never aggregates incompatible intents.
  * it never extends an expired intent -- same intent, same instant as arm R.
  * it never increases residual exposure: it adds a quote on the OPPOSITE side
    of one already contemplated, which can only reduce net exposure if it
    fills, and leaves it unchanged if it does not.

And the pair constraint TIGHTENS rather than loosens: each leg must satisfy the
joint fee-safe pair target, not merely its own ceiling. A pair whose two touches
sum above the target is not placed at all, and that refusal is itself a finding
about the venue.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional, Sequence

from src.l5_shadow import maker as MK

# THREE QUANTITIES, AND 0.96 IS ONLY THE THIRD.
#
#   l5_observed_pair_cost           what L5 actually achieved. A MEASUREMENT.
#   fidelity_predicted_pair_cost    what the behavioural model says L5 would
#                                   TOLERATE in this state. A MODEL OUTPUT.
#   adapted_executable_pair_ceiling what OUR account may economically execute.
#                                   A POLICY, currently 0.96, frozen.
#
# Conflating them is how a policy number becomes a definition of fidelity: if
# the ceiling filters what the model is allowed to propose, then every measured
# "L5 behaviour" is really our own spring rule reflected back, and a model that
# is right about L5 looks wrong. Measured over 1,610 of L5's own two-sided
# windows, 0.96 sits almost exactly on the MEDIAN of his achieved distribution
# -- so as a definition of fidelity it would censor half of him.
#
# The ceiling therefore filters EXECUTION and never the model. Both are
# recorded on every plan, along with what the policy censored.
ADAPTED_EXECUTABLE_PAIR_CEILING = D("0.96")
# Kept as the old name so nothing silently changes meaning mid-flight; it is
# the POLICY, and reading it as anything else is the error above.
PAIR_TARGET = ADAPTED_EXECUTABLE_PAIR_CEILING
# A matched pair costing more than this is a guaranteed loss on the matched leg
# whatever else is true. Formation and economics are SEPARATE GATES: becoming
# two-sided is not success, and arm R's first live pairs cost a median 1.43.
PAR = D("1.00")
LABEL = "MODEL_CANDIDATE/state_driven_two_sided"


def pair_economics(pair_cost: Any) -> Dict[str, Any]:
    """Classify a pair by its ECONOMICS, separately from whether it formed.

    No candidate may call itself successful merely for becoming two-sided. A
    matched pair above par loses money on the matched leg with certainty, and
    it is marked economically negative unless some other EVIDENCED component
    compensates -- which, for a shadow account credited no rebates and no
    rewards, is nothing.
    """
    try:
        # ONE FORMATTING, so this cannot clobber the caller's. Quantizing in
        # two places produced "0.950" here and "0.950000" there for the same
        # number, and whichever wrote last won.
        c = D(str(pair_cost)).quantize(D("0.000001"))
    except Exception:                                         # noqa: BLE001
        return {"pair_economics": "UNKNOWN",
                "why": "pair cost could not be read"}
    if c > PAR:
        return {"pair_economics": "ECONOMICALLY_NEGATIVE", "pair_cost": str(c),
                "loss_per_share": str((c - PAR).quantize(D("0.000001"))),
                "why": ("above par: the matched leg loses this much per share "
                        "with certainty, whatever settles. No evidenced "
                        "component compensates it -- a shadow account is "
                        "credited no rebate and no reward"),
                "eligible_for_live_promotion": False}
    if c > ADAPTED_EXECUTABLE_PAIR_CEILING:
        return {"pair_economics": "POSITIVE_BUT_ABOVE_OUR_CEILING",
                "pair_cost": str(c),
                "why": ("profitable on the matched leg, but outside what our "
                        "account is authorised to execute. This is a POLICY "
                        "limit, not a statement about L5"),
                "eligible_for_live_promotion": False}
    return {"pair_economics": "POSITIVE_AND_EXECUTABLE", "pair_cost": str(c),
            "margin_per_share": str((PAR - c).quantize(D("0.000001"))),
            "eligible_for_live_promotion": True}


def two_sided_plan(intent, terms, *, own_rest: Sequence,
                   opp_rest: Optional[Sequence], venue: str,
                   now: float, pair_target: D = PAIR_TARGET,
                   l5_observed_pair_cost: Any = None,
                   fidelity_predicted_pair_cost: Any = None
                   ) -> Dict[str, Any]:
    """Both legs of a fee-safe pair at this instant, or why there is none.

    `own_rest` is the bid ladder for the outcome L5 just took; `opp_rest` is the
    bid ladder for the complement. `None` for the latter means the book could
    not be read -- UNKNOWN, never empty.
    """
    out: Dict[str, Any] = {
        "venue": venue, "intent_id": intent.id, "arm": "S",
        "label": LABEL, "planned_at": now,
        "market_key": intent.market_key,
        "own_outcome": intent.desired_outcome,
        "opp_outcome": _flip(intent.desired_outcome),
        "qty": str(intent.incremental_qty),
        # the three quantities, kept apart on every row
        "l5_observed_pair_cost": (str(l5_observed_pair_cost)
                                  if l5_observed_pair_cost is not None else None),
        "fidelity_predicted_pair_cost": (
            str(fidelity_predicted_pair_cost)
            if fidelity_predicted_pair_cost is not None else None),
        "adapted_executable_pair_ceiling": str(pair_target),
        "ceiling_is": ("OUR execution policy. It filters what we may trade and "
                       "NEVER what the behavioural model may predict"),
        "pair_target": str(pair_target),
        "hypothesis": ("both legs may rest BEFORE either fills; the reactive "
                       "arm can only quote the complement after the first "
                       "leg has already traded"),
    }
    if terms.tick is None or terms.maker_fee is None:
        return {**out, "admissible": False,
                "reason": "venue did not declare tick or maker fee; refusing "
                          "to price a pair it has not defined"}
    if opp_rest is None:
        return {**out, "admissible": False,
                "reason": "the complement book could not be read: UNKNOWN, "
                          "not an absent side"}

    qty = D(str(intent.incremental_qty))
    own = _leg(own_rest, terms, qty)
    opp = _leg(opp_rest, terms, qty)
    out["own_leg"], out["opp_leg"] = own, opp
    if own["price"] is None or opp["price"] is None:
        empty = [s for s, l in (("own", own), ("opp", opp))
                 if l["price"] is None]
        return {**out, "admissible": False,
                "reason": f"no bid to join on the {'/'.join(empty)} side: the "
                          f"venue cannot host a two-sided pair here at any "
                          f"price"}

    cost = own["effective"] + opp["effective"]
    out["pair_cost"] = str(cost.quantize(D("0.000001")))
    out["pair_cost_headroom"] = str((pair_target - cost).quantize(D("0.000001")))
    out.update(pair_economics(cost))
    if cost > pair_target:
        return {**out, "admissible": False,
                # CENSORED BY POLICY, AND SAID SO. The pair was available and
                # we declined it. Recording that as "no pair here" would make
                # our own ceiling look like a venue fact, and would erase
                # exactly the observations needed to judge the ceiling.
                "censored_by": "adapted_executable_pair_ceiling",
                "available_but_declined": True,
                "reason": (f"the two touches sum to {out['pair_cost']}, above "
                           f"OUR executable ceiling {pair_target}. The pair "
                           f"EXISTS and we decline it; this is our policy, not "
                           f"the venue's limit and not a claim about L5")}
    return {**out, "admissible": True,
            "available_but_declined": False,
            "reason": "both legs rest inside our executable ceiling",
            "legs": [{"outcome": out["own_outcome"], **own},
                     {"outcome": out["opp_outcome"], **opp}]}


def _leg(rest: Sequence, terms, qty: D) -> Dict[str, Any]:
    """Where one leg would rest, and what it really costs per share.

    The ceiling passed to `join_price` is 1 deliberately: the BINDING limit for
    a two-sided plan is the joint pair target, applied once to the sum. Applying
    a per-leg ceiling here as well would refuse pairs that are jointly cheap
    because one side happens to be expensive -- which is the pair economics
    getting overruled by a single-leg rule.
    """
    px = MK.join_price(rest, tick=terms.tick, ceiling=D("1"))
    if px is None:
        return {"price": None, "effective": D("0"), "queue_ahead": None,
                "reason": "no bid on this side"}
    fee = D(str(terms.maker_fee(px, qty)))
    eff = px + (fee / qty if qty else D("0"))
    return {"price": str(px), "effective": eff,
            "fee": str(fee),
            "effective_per_share": str(eff.quantize(D("0.000001"))),
            "queue_ahead": str(MK.queue_ahead(rest, px))}


def _flip(outcome: Any) -> Optional[str]:
    o = str(outcome)
    return "Down" if o == "Up" else ("Up" if o == "Down" else None)
