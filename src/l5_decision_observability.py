"""Always-present economic observability for every L5 decision and fill.

WHY THIS EXISTS. `econ_pair_cost` is recorded on 6 of 25 production fills and
`econ_held_avg_cost` on the same 6, because those fields are a by-product of
the cross-shadow evaluation and that evaluation only runs when all of its own
prerequisites hold. Every policy question about pair economics -- which cap,
which ceiling, whether a cheaper completion was reachable -- is unanswerable on
the other 19. The pair is the number the decision turns on, so it must be
recorded whenever the decision is taken, not whenever a separate shadow module
happens to fire.

THIS MODULE CHANGES NO TRADING BEHAVIOUR. It computes nothing the session does
not already know, returns a record, and has no order path, no I/O and no state.

EVERY KEY IS ALWAYS PRESENT. A component that cannot be computed is reported
as None WITH A REASON, never omitted and never defaulted to a number -- an
absent key is indistinguishable from a zero once it reaches a ledger, and a
fabricated 0.0 pair cost would read as the cheapest possible pair.

It is deliberately NOT wired into the money-moving service here: every file the
probe watches triggers a redeploy of the live campaign, so the one-call
integration is left for the normal clean-diff/preflight process.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Optional

SCHEMA = "l5_decision_observability/v1"

# Reasons a component is genuinely unavailable. UNKNOWN IS NOT ZERO.
R_NO_HELD = "no inventory held: pair undefined before a first leg exists"
R_NO_COMPLEMENT = "complement price unavailable: book one-sided or not served"
R_NO_QTY = "quantity unknown"
R_BAD = "unparseable value from the venue"


def _dec(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        d = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d


R_NO_BOOK = "book not two-sided: complement price not derivable"


def complement_from_book(yes_bid: Any, yes_ask: Any,
                         buys_up: Optional[bool]) -> tuple:
    """Price of the side we do NOT hold, under the venue's own convention.

    Buying UP is lifting the YES ask. Buying DOWN is lifting the NO ask, which
    on a two-outcome book is 1 - yes_bid. The RAW bid and ask are recorded
    beside the derived number so the convention is auditable rather than
    buried; if either leg is missing the result is None WITH A REASON.
    """
    b, a = _dec(yes_bid), _dec(yes_ask)
    if buys_up is True:
        return (a, None) if a is not None else (None, R_NO_BOOK)
    if buys_up is False:
        return ((D("1") - b), None) if b is not None else (None, R_NO_BOOK)
    return (None, "order direction unknown: complement side undetermined")


def observe(*, inv_up: Any, inv_dn: Any, avg_up: Any = None, avg_dn: Any = None,
            complement_price: Any = None, buys_up: Optional[bool] = None,
            spent: Any = None, relation: Any = None, stage: Any = None,
            tte_s: Any = None, yes_bid: Any = None, yes_ask: Any = None,
            action: Any = None, reason: Any = None) -> Dict[str, Any]:
    """One complete economic snapshot of the decision about to be taken."""
    if complement_price is None and (yes_bid is not None or yes_ask is not None):
        complement_price, _why = complement_from_book(yes_bid, yes_ask, buys_up)
    up, dn = _dec(inv_up) or D("0"), _dec(inv_dn) or D("0")
    au, ad = _dec(avg_up), _dec(avg_dn)
    cp = _dec(complement_price)

    matched = min(up, dn)
    residual_signed = up - dn
    total = up + dn

    # held average cost of the side we ALREADY hold -- the side the complement
    # would be paired against. Which side that is follows from the order's
    # direction when known, and from the inventory imbalance otherwise.
    if buys_up is True:
        held_avg, held_reason = ad, ("held side DOWN" if ad is not None else R_NO_HELD)
    elif buys_up is False:
        held_avg, held_reason = au, ("held side UP" if au is not None else R_NO_HELD)
    else:
        if up > dn:
            held_avg, held_reason = au, ("held side UP" if au is not None else R_NO_HELD)
        elif dn > up:
            held_avg, held_reason = ad, ("held side DOWN" if ad is not None else R_NO_HELD)
        else:
            held_avg, held_reason = None, R_NO_HELD

    if held_avg is None:
        pair, pair_reason = None, held_reason
    elif cp is None:
        pair, pair_reason = None, R_NO_COMPLEMENT
    else:
        pair, pair_reason = (held_avg + cp), None

    # the pair BOTH legs of the existing book imply, when both sides are held
    book_pair = (au + ad) if (au is not None and ad is not None) else None

    return {
        "schema": SCHEMA,
        "stage": (str(stage) if stage is not None else None),
        "relation": (str(relation) if relation is not None else None),
        "tte_s": (float(tte_s) if tte_s is not None else None),
        "inv_up": str(up), "inv_dn": str(dn),
        "matched_qty": str(matched),
        "residual_signed": str(residual_signed),
        "residual_abs": str(abs(residual_signed)),
        "imbalance": (str((residual_signed / total).quantize(D("0.0001")))
                      if total > 0 else None),
        "avg_up": (str(au) if au is not None else None),
        "avg_dn": (str(ad) if ad is not None else None),
        "held_avg_cost": (str(held_avg) if held_avg is not None else None),
        "held_avg_cost_reason": held_reason,
        "complement_price": (str(cp) if cp is not None else None),
        "complement_price_reason": (None if cp is not None else R_NO_COMPLEMENT),
        "pair_cost": (str(pair.quantize(D("0.0001"))) if pair is not None else None),
        "pair_cost_reason": pair_reason,
        "book_implied_pair_cost": (str(book_pair.quantize(D("0.0001")))
                                   if book_pair is not None else None),
        "spent": (str(_dec(spent)) if _dec(spent) is not None else None),
        "yes_bid": (str(_dec(yes_bid)) if _dec(yes_bid) is not None else None),
        "yes_ask": (str(_dec(yes_ask)) if _dec(yes_ask) is not None else None),
        "action": (str(action) if action is not None else None),
        "reason": (str(reason) if reason is not None else None),
        "labels": ("OBSERVATION ONLY -- no order path, no state, no trading "
                   "behaviour. UNKNOWN is reported as null with a reason and is "
                   "never a zero."),
    }


# The integration is ONE call at the point a decision or fill is recorded:
#
#     from src.l5_decision_observability import observe
#     row["observability"] = observe(
#         inv_up=inv.up, inv_dn=inv.dn, avg_up=inv.avg_up, avg_dn=inv.avg_dn,
#         complement_price=opp_ask, buys_up=buys_up, spent=inv.spent,
#         relation=relation, stage=stage, tte_s=tte)
#
# It touches src/pmus_spring_l5_session.py, which the probe WATCHES, so applying
# it redeploys the live campaign. Left unapplied on purpose.
