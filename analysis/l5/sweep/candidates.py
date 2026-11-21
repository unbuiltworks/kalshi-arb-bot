"""FROZEN ADAPTED execution candidates. OWNER 2026-09-17, Lane C.

SHADOW / OFFLINE ONLY. Nothing here is imported by the live executor, nothing
here places an order, and none of it is deployed. These are pure functions over
a book snapshot and an inventory state so they can be replayed against a tape.

THE HYPOTHESIS THEY TEST, in the owner's words: FIDELITY may correctly decide
the desired inventory direction while the execution-price translation fails to
respond to live market movement. So FIDELITY IS NOT TOUCHED. Every candidate
takes the relation the frozen model already sampled and changes only how -- or
whether -- that relation reaches the venue.

THE VOCABULARY IS DELIBERATE AND LOAD-BEARING:
  * CROSS      -- the price would be marketable now; post-only rejects it.
  * RESTABLE   -- the price is legal as a maker order and would rest.
  * EXECUTABLE -- restable AND the economics pass.
  * WAIT       -- no legal price satisfies the economics.
  * There is no FILLED. A tape of book states cannot prove a fill, and calling
    a restable quote "filled" is precisely how a sweep talks itself into a bad
    policy. Fill probability is calibrated separately, against real prints.

EVIDENCE BEHIND THE DEFAULTS, so the thresholds are not arbitrary:
  * the live cohort submitted 13 rejections, ALL of them EXTEND, and 11 were
    one repeated price (wire 0.72) that the book ran through between 13:05 and
    13:11 -- identical orders rested 6 times before and were rejected 11 times
    after. That is candidate 1's entire reason to exist.
  * 14 COMPLETEs all rested at 0.49-0.50 while the market went to 0.99, a
    median 34.5 ticks behind the touch. That is candidate 2's.
  * residual_cap forced 423 holds out of 833 decisions, and the cap is tested
    against CURRENT residual rather than the residual the order would create.
    That is candidate 5's.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Mapping, Optional

TICK = D("0.01")
ONE = D("1")

CROSS = "CROSS"
RESTABLE = "RESTABLE"
EXECUTABLE = "EXECUTABLE"
WAIT = "WAIT"


def _d(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def would_cross(side_yes: bool, wire_px: D, yes_bid: Optional[D],
                yes_ask: Optional[D]) -> Optional[bool]:
    """Post-only semantics on this venue's wire convention.

    `price` on the wire is ALWAYS the long/YES side's price. A YES buy lifts
    the offer when its wire price reaches the ask. A NO buy is a YES-side sell,
    so it hits the bid when its wire price falls to the bid. Getting this
    backwards inverts every candidate, which is why it is one function used by
    all of them rather than repeated per candidate.

    None when the relevant side is absent -- unknown is never "safe"."""
    if side_yes:
        return None if yes_ask is None else wire_px >= yes_ask
    return None if yes_bid is None else wire_px <= yes_bid


def nearest_legal_maker(side_yes: bool, yes_bid: Optional[D],
                        yes_ask: Optional[D], behind_ticks: int = 0
                        ) -> Optional[D]:
    """The most aggressive wire price that still rests, optionally stepped back.

    `behind_ticks=0` joins the touch; 1 sits one tick behind it. Returns None
    on a one-sided book -- which is not a corner case here: the 2026-09-17
    residual was stranded by exactly that, `offers: []` with 49 bid levels."""
    if side_yes:
        if yes_ask is None:
            return None
        px = yes_ask - TICK - (TICK * behind_ticks)
    else:
        if yes_bid is None:
            return None
        px = yes_bid + TICK + (TICK * behind_ticks)
    if px <= 0 or px >= ONE:
        return None
    return px


# --------------------------------------------------------------------------
# candidate 1 -- book-aware EXTEND placement

def extend_placement(mode: str, side_yes: bool, target_px: D,
                     yes_bid: Optional[D], yes_ask: Optional[D]) -> dict:
    """`mode` in {pair_target, nearest_maker, one_behind}.

    pair_target is the CURRENT behaviour, kept as the control: submit the price
    the pair economics wanted, regardless of where the book has moved. It is
    included precisely so the comparison is against what we actually do."""
    if mode == "pair_target":
        px = target_px
    elif mode == "nearest_maker":
        px = nearest_legal_maker(side_yes, yes_bid, yes_ask, 0)
    elif mode == "one_behind":
        px = nearest_legal_maker(side_yes, yes_bid, yes_ask, 1)
    else:
        raise ValueError("unknown extend placement mode: %r" % mode)
    if px is None:
        return {"mode": mode, "outcome": WAIT, "px": None,
                "why": "no legal maker price on a one-sided or absent book"}
    x = would_cross(side_yes, px, yes_bid, yes_ask)
    if x is None:
        return {"mode": mode, "outcome": WAIT, "px": str(px),
                "why": "crossing undecidable: that side of the book is absent"}
    if x:
        return {"mode": mode, "outcome": CROSS, "px": str(px),
                "why": "post-only would reject: marketable at submission"}
    return {"mode": mode, "outcome": RESTABLE, "px": str(px),
            "why": "legal maker price"}


# --------------------------------------------------------------------------
# candidate 2 -- book-aware COMPLETE (the owner's highest priority)

def complete_plan(side_yes: bool, held_avg: D, held_qty: D,
                  yes_bid: Optional[D], yes_ask: Optional[D],
                  max_pair_cost: D) -> dict:
    """Best EXECUTABLE maker complement, and what pair it produces.

    The question this exists to answer: is accepting a worse pair cost to kill
    a dangerous residual better than leaving the complement far behind the
    touch and carrying directional exposure to settlement? The live cohort ran
    the second arm of that experiment by accident and the residual stranded.

    `max_pair_cost` is swept and every value is labelled ADAPTED. Note that
    <1.00 is NOT automatically profitable: fees are not modelled here, so the
    output reports pair cost and the caller applies known fees."""
    px = nearest_legal_maker(side_yes, yes_bid, yes_ask, 0)
    if px is None:
        return {"outcome": WAIT, "px": None, "pair_cost": None,
                "why": "no legal maker complement: book one-sided"}
    # the complement's cost in outcome terms
    cost = px if side_yes else (ONE - px)
    pair = held_avg + cost
    row = {"px": str(px), "complement_cost": str(cost), "pair_cost": str(pair),
           "max_pair_cost": str(max_pair_cost),
           "residual_reduction": str(held_qty),
           "labels": "ADAPTED threshold; fees NOT netted; pair<1 is not "
                     "automatically profitable after fees"}
    if would_cross(side_yes, px, yes_bid, yes_ask):
        row.update(outcome=CROSS, why="post-only would reject")
        return row
    if pair > max_pair_cost:
        row.update(outcome=WAIT,
                   why="pair %s exceeds ADAPTED ceiling %s" % (pair, max_pair_cost))
        return row
    row.update(outcome=EXECUTABLE, why="restable and within the ADAPTED ceiling")
    return row


# --------------------------------------------------------------------------
# candidate 3 -- residual-aware EXTEND suppression

def extend_suppression(residual_units: D, cap_units: D,
                       actions_since_residual: int,
                       adverse_move: Optional[D],
                       complement_drift: Optional[D],
                       one_sided: bool,
                       mode: str = "linear") -> dict:
    """Should this EXTEND be suppressed? Returns a suppression probability in
    [0,1] and the binding reason, so the sweep can attribute outcomes.

    `off` is the control -- today's behaviour, which suppresses nothing and
    lets residual grow until the cap refuses the NEXT action."""
    if mode == "off":
        return {"suppress_p": 0.0, "reason": "control: no suppression"}
    reasons = []
    p = D("0")
    if cap_units > 0:
        frac = residual_units / cap_units
        if frac > 0:
            p = max(p, min(ONE, frac))
            reasons.append("residual %s of cap %s" % (residual_units, cap_units))
    if actions_since_residual >= 3:
        p = max(p, D("0.5"))
        reasons.append("residual persisted %d actions" % actions_since_residual)
    if adverse_move is not None and adverse_move > 0:
        p = max(p, min(ONE, adverse_move * D("5")))
        reasons.append("probability moved %s against the residual" % adverse_move)
    if complement_drift is not None and complement_drift > 0:
        p = max(p, min(ONE, complement_drift * D("5")))
        reasons.append("complement drifting away by %s" % complement_drift)
    if one_sided:
        p = ONE
        reasons.append("book one-sided: completion is impossible")
    if mode == "hard" and p > 0:
        p = ONE
    return {"suppress_p": str(p),
            "reason": "; ".join(reasons) or "no suppression condition met"}


# --------------------------------------------------------------------------
# candidate 4 -- matched-inventory preservation

def acquisition_gate(matched_units: D, residual_units: D,
                     min_matched_to_protect: D, mode: str = "off") -> dict:
    """Once meaningful inventory is MATCHED, should a further same-side EXTEND
    need stronger evidence than the initial OPEN did?

    This attacks the observed pattern directly: on 2026-09-17 the engine
    reached matched 0.40/0.40 with residual 0.00 -- a completed, safe position
    -- and then rebuilt directional exposure to 0.59/0.40 that it could not
    unwind when the book went one-sided."""
    if mode == "off":
        return {"allow": True, "reason": "control: acquisition unrestricted"}
    protected = matched_units >= min_matched_to_protect
    if not protected:
        return {"allow": True,
                "reason": "matched %s below protection floor %s"
                          % (matched_units, min_matched_to_protect)}
    if mode == "wait":
        return {"allow": False,
                "reason": "matched %s protected: WAIT rather than rebuild "
                          "directional exposure" % matched_units}
    if mode == "require_flat":
        allow = residual_units == 0
        return {"allow": allow,
                "reason": ("residual is zero; a new leg is a fresh sequence"
                           if allow else
                           "matched inventory protected and residual %s is "
                           "already open" % residual_units)}
    raise ValueError("unknown acquisition gate mode: %r" % mode)


# --------------------------------------------------------------------------
# candidate 5 -- proposed-order residual cap

def proposed_residual_ok(residual_units: D, order_units: D, same_side: bool,
                         cap_units: D) -> dict:
    """Test the cap against the residual the order WOULD CREATE.

    The live cap is a pre-trade admission test on the next action, not a bound
    on the resulting position, which is how lifecycle 2 reached residual 3.8636
    against a cap of 3 -- ~29% over, breaching no monetary control but breaching
    the stated bound. This is the smallest correct form of that test."""
    after = residual_units + order_units if same_side \
        else max(D("0"), residual_units - order_units)
    ok = after <= cap_units
    return {"allow": ok, "residual_before": str(residual_units),
            "residual_after_if_filled": str(after), "cap": str(cap_units),
            "reason": ("within cap" if ok else
                       "order would leave residual %s above cap %s"
                       % (after, cap_units))}


#: The frozen sweep grid. Declared here, before any tape is analysed, so the
#: candidate set cannot drift toward whatever the data happens to favour.
GRID = {
    "extend_placement": ("pair_target", "nearest_maker", "one_behind"),
    "complete_max_pair_cost": ("0.96", "0.98", "1.00", "1.02", "1.05"),
    "extend_suppression": ("off", "linear", "hard"),
    "acquisition_gate": ("off", "wait", "require_flat"),
    "proposed_residual_cap": (False, True),
}
