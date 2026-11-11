"""Favour the order that reduces directional exposure; price the one that adds.

WHY THIS EXISTS, from settled live evidence. Across the 2026-11-02
0145z-0230z campaign the engine acquired a surplus leg in three of four
windows -- 4.09 UP against 2.04 DOWN at 0145z, 3.83 DOWN against 1.98 UP at
0200z, 3.83 DOWN against 1.89 UP at 0230z. Matched pairs earned
+$0.270564 in total. The unmatched remainder swung -$0.953225 to +$1.191300
PER WINDOW. A completed pair is a ~+$0.06 business; a stranded leg is a
~+/-$1 lottery ticket, and we were buying one of each.

WHAT THIS IS NOT. It is not a halt. Trading does not stop because a residual
exists: a complementary order is ALWAYS permitted by this gate, and a market
that is balanced can open a fresh cycle immediately. Only an addition to the
side that is ALREADY overweight has to argue for itself.

=========================================================================
TWO DIFFERENT QUESTIONS, TWO DIFFERENT CONVENTIONS. READ THIS BEFORE
CHANGING ANYTHING HERE.
=========================================================================

`l5_live_execution.residual_cap_decision` bounds RISK, and to do that it
assumes every still-fillable order on our own side fills while nothing
resting on the other side fills at all -- "a resting opposite-side leg is an
intention, never a completed hedge". That is right for a worst case.

It is WRONG for direction. Under that convention a complementary order never
reduces anything, because the hedge it represents is discounted to zero --
so a gate built on it would refuse the very completions this strategy needs.

So DIRECTION is judged on POTENTIAL FILLS: what we would hold if every one
of our own live orders filled, both sides counted. That is the question
"does this action move us toward or away from balance?", and it is the
question the owner asked.

Both still apply. This gate decides whether an action is pointed the right
way; the residual cap still decides whether it is too big. Neither replaces
the other, and this module changes no limit.
"""
from __future__ import annotations

import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Optional

#: OFF BY DEFAULT and gated by its own variable, so its effect can be
#: measured apart from the event-driven wakeups and apart from the requote
#: queue gate. Three changes landing together would be three changes none of
#: which could be attributed.
ENV = "L5_INVENTORY_PREFERENCE"

UP, DOWN = "UP", "DOWN"

#: THE BAR AN OVERWEIGHT ADDITION MUST CLEAR, per contract.
#:
#: It is not zero, and that is the whole point. Adding to the overweight side
#: creates residual exposure, and residual exposure has a MEASURED cost:
#: -$0.2315 per unit over 384 windows, with adverse_rate 0.89 and
#: swept_share 0.98. An addition whose expected value is merely positive
#: still loses money if it is below what the exposure it creates costs.
#:
#: So the threshold is that measured cost. Anything cheaper is refused.
MIN_INCREMENTAL_EV_USD = D("0.2315")

#: Where the -0.2315 comes from, carried so a reader can find it.
EV_THRESHOLD_BASIS = ("measured residual adverse selection, -$0.2315/unit "
                      "over 384 windows (adverse_rate 0.89, swept_share "
                      "0.98)")


def enabled() -> bool:
    raw = (os.getenv(ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def potential_net(held_up: Any, held_dn: Any,
                  resting_up: Any = 0, resting_dn: Any = 0
                  ) -> Optional[D]:
    """Signed exposure if every one of OUR OWN live orders filled.

    Positive is UP-heavy, negative DOWN-heavy, zero balanced. Both sides'
    resting orders count, which is what makes a complementary order read as
    a reduction. See the module docstring for why this convention differs
    from the residual cap's.

    None means some input was unreadable, and the caller must treat that as
    unknown rather than as balanced.
    """
    hu, hd = _d(held_up), _d(held_dn)
    ru, rd = _d(resting_up), _d(resting_dn)
    if hu is None or hd is None or ru is None or rd is None:
        return None
    return (hu + ru) - (hd + rd)


def classify(*, held_up: Any, held_dn: Any,
             resting_up: Any = 0, resting_dn: Any = 0,
             side: Any, qty: Any) -> Dict[str, Any]:
    """Does this order move us toward balance, away from it, or open a cycle?

    OPENING is its own class and is NEVER an accumulation. From balance,
    every first leg necessarily increases |exposure| -- treating that as an
    increase would refuse all opening legs and stop the market, which is
    precisely what this gate must not do.
    """
    s = str(side).upper()
    if s not in (UP, DOWN):
        return {"class": "UNKNOWN", "reason": "SIDE_UNKNOWN",
                "side": str(side)}
    q = _d(qty)
    if q is None or q <= 0:
        return {"class": "UNKNOWN", "reason": "QTY_UNREADABLE",
                "qty": str(qty)}
    before = potential_net(held_up, held_dn, resting_up, resting_dn)
    if before is None:
        return {"class": "UNKNOWN", "reason": "INVENTORY_UNREADABLE"}
    after = before + q if s == UP else before - q
    out = {"side": s, "qty": str(q),
           "potential_net_before": str(before),
           "potential_net_after": str(after),
           "abs_before": str(abs(before)), "abs_after": str(abs(after))}
    if before == 0:
        out["class"] = "OPENING"
        out["reason"] = None
        return out
    if abs(after) < abs(before):
        out["class"] = "REDUCING"
    elif abs(after) == abs(before):
        # a perfect flip: same magnitude, other side. It neither helps nor
        # harms the imbalance, so it is not charged the EV bar.
        out["class"] = "NEUTRAL"
    else:
        out["class"] = "INCREASING"
    out["reason"] = None
    return out


def decide(*, held_up: Any, held_dn: Any,
           resting_up: Any = 0, resting_dn: Any = 0,
           side: Any, qty: Any,
           ev_usd_per_contract: Any = None,
           ev_provenance: Any = None,
           min_ev_usd_per_contract: Any = None) -> Dict[str, Any]:
    """Permit the action, or say why it must argue its case first.

    REDUCING, NEUTRAL and OPENING all pass untouched: complementary
    execution stays aggressive and repeated cycles keep running.

    INCREASING must show a DEFENSIBLE incremental expected value -- a
    number AND where it came from, clearing MIN_INCREMENTAL_EV_USD. Absent
    evidence is refused, never read as positive: an EV we cannot produce is
    not an EV of zero, it is an unknown, and an unknown must not authorise
    fresh directional exposure.

    THIS GATE MOVES NO LIMIT. A pass here still faces the residual cap, the
    worst-case deficit bound, the exposure ceiling, the cash cap and every
    authorization check, unchanged.
    """
    cls = classify(held_up=held_up, held_dn=held_dn,
                   resting_up=resting_up, resting_dn=resting_dn,
                   side=side, qty=qty)
    k = cls.get("class")
    if k == "UNKNOWN":
        # fails closed: an unreadable inventory cannot authorise anything.
        return dict(cls, act=False)
    if k in ("REDUCING", "NEUTRAL", "OPENING"):
        return dict(cls, act=True)

    bar = _d(min_ev_usd_per_contract)
    if bar is None:
        bar = MIN_INCREMENTAL_EV_USD
    ev = _d(ev_usd_per_contract)
    base = dict(cls, ev_threshold_usd=str(bar),
                ev_threshold_basis=EV_THRESHOLD_BASIS)
    if ev is None:
        return dict(base, act=False, reason="EV_EVIDENCE_ABSENT",
                    labels=("no expected-value estimate was supplied for an "
                            "addition to the overweight side. Absent "
                            "evidence is not positive expected value."))
    prov = str(ev_provenance or "").strip()
    if not prov:
        return dict(base, act=False, reason="EV_PROVENANCE_ABSENT",
                    ev_usd_per_contract=str(ev),
                    labels=("an expected value with no stated source cannot "
                            "be audited, so it cannot be defensible."))
    if ev <= bar:
        return dict(base, act=False, reason="EV_BELOW_THRESHOLD",
                    ev_usd_per_contract=str(ev), ev_provenance=prov,
                    labels=("the addition must beat the measured cost of "
                            "the residual exposure it creates, not merely "
                            "be positive."))
    return dict(base, act=True, class_detail="INCREASING_EV_JUSTIFIED",
                ev_usd_per_contract=str(ev), ev_provenance=prov)
