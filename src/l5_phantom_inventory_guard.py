"""The three invariants that would have prevented the 0.1300 DOWN phantom.

PURE FUNCTIONS. No network, no order path, no state file, no arming key.

WHAT HAPPENED, from the authoritative record (see
analysis/l5/evidence/recon_1345z/VERDICT.md). Order `D0VC6VJDMZ7J` rested in
`cpc-btc-updown-15m-2026-10-10-1345z` as BUY / OUTCOME_SIDE_YES, limit 0.62,
quantity 0.13, and was CANCELLED with `cumQuantity 0` and `avgPx 0.0000`.
Twenty-seven seconds later our book held **0.1300 DOWN** with no cost basis
and `fills_booked 0`, and `INCOHERENT_BOOK` then failed every settlement and
every submission closed -- permanently.

The fingerprint identifies the writer exactly: a position with NO `leg_cost`
and `fill_count == 0` can only come from `reconcile_from_venue`'s branch for
"a market the venue holds and we have never booked", and the DOWN side can
only come from `position_row`'s `bought - sold` rule, which returns DOWN
whenever the venue's row shows sold exceeding bought. So a venue row arrived
whose halves implied a 0.13 short while no fill of ours existed.

THE PAYLOAD THAT CAUSED IT WAS NEVER LOGGED, which is itself the first
defect: only the resulting `drift` was recorded, so the cause is
unreconstructable after the fact. Invariant 1 fixes that. Invariants 2 and 3
stop the write and give it a terminal transition.

NONE OF THESE INVENT A COST BASIS and none of them is a force-clear:
  1. a cost-less adoption RECORDS the row that caused it;
  2. a cost-less adoption is REFUSED where our own authoritative order
     records show nothing filled -- an unexplained venue row is not
     inventory, and the book is still marked uncertain, so nothing is
     silently passed;
  3. a cost-less quantity with zero fills is DROPPED only once the venue
     authoritatively reports flat -- there was never a fill to lose, and the
     drop is logged with its anchor.

Integrating these into `l5_live_execution.InventoryStore` is a reviewed
change on the executor branch and is NOT made here.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

#: a venue position row with no cost of ours behind it
COST_LESS = "cost_less_venue_quantity"


def _q(v: Any) -> D:
    return D(str(v if v is not None else 0))


def adoption_provenance(market: str, side: str, qty: Any,
                        venue_row: Mapping[str, Any]) -> dict:
    """INVARIANT 1. What must be stored when a cost-less quantity is written.

    The row is kept verbatim apart from nothing -- venue position rows carry
    no credentials -- so the next occurrence is diagnosable from the state
    file alone instead of from a log that may have rotated.
    """
    return {"kind": COST_LESS, "market": str(market), "side": str(side),
            "qty": str(_q(qty)), "venue_row": dict(venue_row),
            "why": ("written with no leg_cost because no fill of ours "
                    "attested a price; recorded so the cause survives")}


def refuse_cost_less_adoption(*, fill_count: int,
                              our_orders: Sequence[Mapping[str, Any]],
                              ) -> Optional[str]:
    """INVARIANT 2. May a cost-less venue quantity be written at all?

    NO when our own authoritative order records for that market say nothing
    filled: `cumQuantity == 0` on every one of them and no booked fill. Such
    a row contradicts the venue's own order history, and the honest state is
    an unexplained discrepancy -- which belongs in the uncertainty flag, not
    in `positions`, where it becomes phantom inventory that fails settlement
    closed forever.

    Returns a refusal reason, or None when the write is admissible (we do
    have fills, so a quantity we cannot cost is a real reconciliation gap).
    """
    if int(fill_count or 0) > 0:
        return None
    for o in our_orders or ():
        if _q(o.get("cumQuantity")) > 0:
            return None
    return ("venue_quantity_contradicts_our_order_records:"
            "fill_count=0,all_orders_cumQuantity=0")


def may_drop_cost_less(*, qty: Any, fill_count: int, leg_cost: Any,
                       venue_flat: bool) -> bool:
    """INVARIANT 3. May a cost-less quantity finally leave the book?

    Only when ALL of these hold: it has no cost basis, no fill ever attested
    it, there is a quantity to drop, and the venue authoritatively reports
    flat for it. Then there was never a fill to lose and nothing to settle,
    so dropping it posts no P&L and loses no economics. Any other
    combination keeps it, which is what makes this not a force-clear.
    """
    return bool(venue_flat
                and int(fill_count or 0) == 0
                and _q(leg_cost) == 0
                and _q(qty) > 0)


def drop_record(market: str, side: str, qty: Any,
                anchor: Mapping[str, Any]) -> dict:
    """The audit row a drop writes. No P&L, and the anchor is verbatim."""
    return {"kind": "cost_less_quantity_dropped", "market": str(market),
            "side": str(side), "qty": str(_q(qty)), "settlement_pnl": "0",
            "closed_against": "AUTHORITATIVE_FLAT_WITH_ZERO_FILLS",
            "anchor": dict(anchor),
            "why": ("no fill ever attested this quantity and the venue "
                    "reports flat, so there is nothing to settle and no "
                    "P&L to post")}
