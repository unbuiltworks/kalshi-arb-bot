"""ONE order-state machine, built on the classifiers that have traded live.

There were two. `pmus_q1_executor` -- the executor that has placed, filled
and settled real PMUS orders -- submits, then POLLS `/v1/order/{id}` and
classifies the venue's own record:

    order_state_str(order)     the state, however the venue spells it
    _filled_qty(order)         cumQuantity / filledQuantity / cumQty
    terminal_rejection(order)  REJECTED with zero fill: TERMINAL no-fill,
                               never a halt, never retried
    authoritative_fill(order)  {price, qty}, with the NO -> own-terms
                               inversion, or a Halt when the record says
                               filled and no price can be parsed

`pmus_native_money_path` had a different, and non-functional, arrangement:
`AuthenticatedVenue.unbooked_fills()` asked `self.tp.fills()` behind
`hasattr(self.tp, "fills")`. **`PmusTransport` has no `fills` method**, so in
production that guard was always false, the sweep always returned None, and
`_book_authenticated_fills()` always returned []. Every integration test
injected a venue double that did implement `fills()`.

That is why the R4 window (2026-09-29 0045z) filled 10 UP at 0.26 at
00:46:46 and ran 215 more ticks believing it was flat, and why its complement
`ORDER_STATE_REJECTED` was never noticed: nothing on the native path ever
read an order back unless a LOCK transaction or a taker leg asked.

This module is the single classifier both paths now use. It reuses q1's
generic predicates verbatim and adds only what q1 coupled to its own fixed
`QTY`: the average price, in the outcome's OWN terms.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import pmus_q1_executor as Q1X

ONE = D(1)

#: the classifications. TERMINAL_* mean the venue will not change its mind.
FILLED = "FILLED"
PARTIAL = "PARTIALLY_FILLED"
REJECTED_NO_FILL = "REJECTED_NO_FILL"
CANCELLED_NO_FILL = "CANCELLED_NO_FILL"
WORKING = "WORKING"
UNKNOWN = "UNKNOWN"

TERMINAL_CLASSES = (FILLED, REJECTED_NO_FILL, CANCELLED_NO_FILL)

#: states the venue uses for an order it has finished with. CANCELED and
#: CANCELLED are both accepted because the venue's own vocabulary has used
#: both spellings and a classifier that knows only one fails open.
_DONE = ("FILLED", "REJECT", "CANCEL", "EXPIRED", "DONE_FOR_DAY")


class AmbiguousFill(RuntimeError):
    """The record says filled and carries no usable price. Never guessed."""


def own_terms_price(order: Mapping[str, Any], *,
                    side: Optional[str] = None) -> Optional[D]:
    """The average execution price in the OUTCOME'S OWN TERMS, or None.

    The venue serves the LONG-side price, so a NO/DOWN fill's own-terms price
    is `1 - avgPx` (docs.polymarket.us, Create Order). `side` lets a caller
    that knows which side it sent override the record's own labelling; the
    record is used when it does not.

    Mirrors `pmus_q1_executor.authoritative_fill`'s price extraction, minus
    that function's coupling to its module-level QTY.
    """
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        v = order.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is None:
            continue
        try:
            px = D(str(v))
        except Exception:                                      # noqa: BLE001
            continue
        if px <= 0:
            continue
        want_no = False
        if side is not None:
            want_no = str(side).strip().lower() in ("down", "no",
                                                    "outcome_side_no")
        else:
            want_no = (str(order.get("outcomeSide") or "").upper()
                       .endswith("NO")
                       or "SHORT" in str(order.get("intent") or "").upper())
        return (ONE - px) if want_no else px
    return None


def commission(order: Mapping[str, Any]) -> Optional[D]:
    """The venue's own `commissionNotionalTotalCollected`, or None.

    Returned with the venue's sign and NOT reinterpreted: R4's filled maker
    order carried `-0.0200`, i.e. collected-from-us was negative. Whether
    that is called a rebate is an accounting decision for the caller, not a
    fact this classifier may invent.
    """
    for key in ("commissionNotionalTotalCollected", "commission",
                "commissionNotional"):
        v = order.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is None:
            continue
        try:
            return D(str(v))
        except Exception:                                      # noqa: BLE001
            continue
    return None


def classify(order: Optional[Mapping[str, Any]], *,
             requested_qty: Optional[Any] = None,
             side: Optional[str] = None) -> Dict[str, Any]:
    """One venue order record -> one authoritative lifecycle verdict.

    UNKNOWN is returned for a record we could not read, and UNKNOWN is never
    silently a zero fill: a caller must halt rather than release budget or
    forget an order on the strength of a read that did not happen.
    """
    if not order:
        return {"classification": UNKNOWN, "terminal": False,
                "state": "", "filled_qty": None, "price_own": None,
                "commission": None,
                "why": "no order record returned by the venue"}
    state = Q1X.order_state_str(order)
    filled = Q1X._filled_qty(order)
    fee = commission(order)
    px = own_terms_price(order, side=side)
    want = None
    if requested_qty is not None:
        try:
            want = D(str(requested_qty))
        except Exception:                                      # noqa: BLE001
            want = None

    if Q1X.terminal_rejection(order):
        return {"classification": REJECTED_NO_FILL, "terminal": True,
                "state": state, "filled_qty": D(0), "price_own": None,
                "commission": fee,
                "why": "venue REJECTED with zero cumulative quantity"}
    if filled > 0 and px is None:
        raise AmbiguousFill(
            "order reports filled %s and carries no parseable average "
            "execution price" % filled)
    done = any(tok in state for tok in _DONE)
    if filled > 0:
        full = "FILLED" in state or (want is not None and filled >= want)
        return {"classification": FILLED if (full or done) else PARTIAL,
                "terminal": bool(full or done), "state": state,
                "filled_qty": filled, "price_own": px, "commission": fee,
                "why": None}
    if done:
        return {"classification": CANCELLED_NO_FILL, "terminal": True,
                "state": state, "filled_qty": D(0), "price_own": None,
                "commission": fee,
                "why": "venue finished with this order and it never filled"}
    return {"classification": WORKING, "terminal": False, "state": state,
            "filled_qty": D(0), "price_own": None, "commission": fee,
            "why": None}
