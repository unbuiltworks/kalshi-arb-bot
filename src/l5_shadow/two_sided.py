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

# Spring discipline, per CLAUDE.md: "materially cheaper pair completion
# (roughly <= 0.96)". Near-$1 pair tolerance belongs to later incentive-era L5
# behaviour and stays locked. This is a POLICY number, not a venue term, and it
# is only ever used to REFUSE a pair -- never to justify paying more for one.
PAIR_TARGET = D("0.96")
LABEL = "MODEL_CANDIDATE/state_driven_two_sided"


def two_sided_plan(intent, terms, *, own_rest: Sequence,
                   opp_rest: Optional[Sequence], venue: str,
                   now: float, pair_target: D = PAIR_TARGET) -> Dict[str, Any]:
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
    if cost > pair_target:
        return {**out, "admissible": False,
                "reason": (f"the two touches sum to {out['pair_cost']}, above "
                           f"the fee-safe target {pair_target}. Paying it is "
                           f"forbidden, so no pair is available here -- a fact "
                           f"about the venue, not a knob")}
    return {**out, "admissible": True,
            "reason": "both legs rest inside the fee-safe pair target",
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
