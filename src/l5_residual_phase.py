"""WHERE A WINDOW'S RESIDUAL ACTUALLY IS, as one named phase. PURE.

WHY A PROJECTION AND NOT A STORED MACHINE. Every fact this needs is
already durable somewhere authoritative: order states live in the
executor's ledger (`NEW/LIVE/FILLED/CANCELLED/UNCERTAIN`), inventory
comes from our own fills, and the halt latch is its own durable record.
A second stored copy of the same truth is the duplicate-ledger mistake
this codebase keeps paying for -- two records of one fact disagree, and
then nothing knows which is right. So this is a FUNCTION of durable
state, not another thing to keep in step with it.

WHAT DRIVES A TRANSITION. Venue-confirmed order state and filled
quantity, and nothing else. Not elapsed time, and NOT a submission
acknowledgment: the venue's own ack has been observed saying
`filled_qty 0` for an order that had in fact filled (recorded in
`l5_pmus_transport.order`), so an ack is a receipt that a request was
heard, never evidence of a fill. A phase therefore moves when the order
row the venue serves says it moved.

THE ONE PHASE THAT BLOCKS ENTRIES. `RECONCILING` means at least one of
our orders is in an uncertain state -- an unresolved submit, or a cancel
we asked for and have not yet read terminal. `blocks_new_entries` is
True there, because opening more exposure while we cannot say what we
already hold is the specific way a bounded probe stops being bounded.

FAST DETECTION IS NOT FAST LIQUIDATION. Reaching
`COMPLETION_EVALUATION` quickly is the point -- the residual is visible
within a cycle of the fill that created it. What happens next is an
economic question answered elsewhere (`l5_inventory_decision.rank`), and
this module deliberately cannot answer it: it reports WHERE the residual
is, never what to pay to get rid of it. A timer must not be able to
force a loss.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

IDLE = "IDLE"
ORDERS_PENDING = "ORDERS_PENDING"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
COMPLETION_EVALUATION = "COMPLETION_EVALUATION"
COMPLETING = "COMPLETING"
REDUCING = "REDUCING"
HOLDING = "HOLDING"
RECONCILING = "RECONCILING"
COMPLETE = "COMPLETE"
HALTED = "HALTED"

PHASES = (IDLE, ORDERS_PENDING, PARTIALLY_FILLED, COMPLETION_EVALUATION,
          COMPLETING, REDUCING, HOLDING, RECONCILING, COMPLETE, HALTED)

#: phases in which NO new entry may be opened
NO_ENTRY = (RECONCILING, HALTED, COMPLETE)

#: order states, mirroring l5_btc5m_execution so the two cannot drift
OPEN_STATES = ("NEW", "LIVE", "UNCERTAIN")
UNCERTAIN = "UNCERTAIN"

#: reasons that mark an order as a completion or a reduction
COMPLETION_REASONS = ("STRADDLE_COMPLETION", "HEDGE_UNDERDOG",
                      "MAKER_COMPLETE", "TAKER_COMPLETE")
REDUCING_REASONS = ("DEFENSIVE_UNWIND", "REDUCE", "EXIT", "TAKER_PARTIAL")


def _d(x: Any) -> Optional[D]:
    if x is None:
        return None
    try:
        v = D(str(x))
    except Exception:
        return None
    return v if v.is_finite() else None


def phase(*, orders: Sequence[Mapping[str, Any]],
          inventory: Mapping[str, Mapping[str, Any]],
          halted: Any = False,
          window_closed: Any = False,
          chosen_action: Any = None) -> dict:
    """The residual's phase, and whether entries are blocked.

    `orders` are OUR order rows for one market, as the ledger holds them
    -- each with `state`, `filled_qty`, `reason` and optionally
    `cancel_requested`. `inventory` is {side: {"qty","cost"}} from our
    own fills. `chosen_action` is what the economic ranking picked, and
    is the ONLY way COMPLETION_EVALUATION is distinguished from HOLDING:
    both have an unmatched residual and nothing working, and which one
    we are in is a statement about the decision, not about the book.

    Returns `{"phase", "blocks_new_entries", "why", "unmatched_qty",
    "unmatched_side", "matched_qty", "open_orders", "uncertain"}`.
    """
    rows = [r for r in (orders or []) if isinstance(r, Mapping)]
    states = [str(r.get("state") or "") for r in rows]
    uncertain = [r for r in rows if str(r.get("state") or "") == UNCERTAIN]
    #: a cancel we asked for and have not read terminal is equally
    #: unresolved: the order may still be working or may have filled
    pending_cancel = [r for r in rows
                      if r.get("cancel_requested")
                      and str(r.get("state") or "") in OPEN_STATES]
    open_rows = [r for r in rows
                 if str(r.get("state") or "") in OPEN_STATES]

    inv = inventory or {}
    qty = {}
    for side, leg in inv.items():
        v = _d((leg or {}).get("qty")) if isinstance(leg, Mapping) else None
        qty[side] = v if v is not None else D(0)
    sides = sorted(qty)
    a = qty.get(sides[0], D(0)) if sides else D(0)
    b = qty.get(sides[1], D(0)) if len(sides) > 1 else D(0)
    matched = min(a, b)
    if a > b:
        unmatched, unmatched_side = a - b, sides[0]
    elif b > a:
        unmatched, unmatched_side = b - a, sides[1] if len(sides) > 1 else None
    else:
        unmatched, unmatched_side = D(0), None
    filled_any = any((_d(r.get("filled_qty")) or D(0)) > 0 for r in rows)

    def out(ph: str, why: str) -> dict:
        return {"phase": ph, "blocks_new_entries": ph in NO_ENTRY,
                "why": why, "unmatched_qty": str(unmatched),
                "unmatched_side": unmatched_side,
                "matched_qty": str(matched),
                "open_orders": len(open_rows),
                "uncertain": len(uncertain) + len(pending_cancel)}

    #: ORDER MATTERS. Halt outranks everything, then anything we cannot
    #: account for, then the ordinary lifecycle.
    if bool(halted):
        return out(HALTED, "HALT_LATCH_ASSERTED")
    if uncertain:
        return out(RECONCILING, "ORDER_STATE_UNCERTAIN")
    if pending_cancel:
        return out(RECONCILING, "CANCEL_REQUESTED_NOT_YET_TERMINAL")
    if unmatched > 0:
        working_completion = [r for r in open_rows
                              if str(r.get("reason") or "")
                              in COMPLETION_REASONS]
        working_reduction = [r for r in open_rows
                             if str(r.get("reason") or "")
                             in REDUCING_REASONS]
        if working_completion:
            return out(COMPLETING, "COMPLETION_ORDER_WORKING")
        if working_reduction:
            return out(REDUCING, "REDUCING_ORDER_WORKING")
        if str(chosen_action or "").upper() in ("HOLD", "HOLDING"):
            #: a decision was made and it was to carry the residual. Not
            #: a failure to decide, and not a timer's doing.
            return out(HOLDING, "RANKING_CHOSE_HOLD")
        return out(COMPLETION_EVALUATION, "UNMATCHED_INVENTORY_PRESENT")
    if open_rows:
        return out(PARTIALLY_FILLED if filled_any else ORDERS_PENDING,
                   "FILLS_WITH_ORDERS_STILL_OPEN" if filled_any
                   else "ORDERS_WORKING_NO_FILLS")
    if matched > 0 or filled_any:
        #: flat of unmatched risk with nothing working. COMPLETE only
        #: once the window is shut: before that the pair can still grow.
        return out(COMPLETE if bool(window_closed) else IDLE,
                   "MATCHED_AND_NOTHING_WORKING" if bool(window_closed)
                   else "MATCHED_BUT_WINDOW_STILL_OPEN")
    return out(COMPLETE if bool(window_closed) else IDLE,
               "NOTHING_HELD_AND_NOTHING_WORKING")
