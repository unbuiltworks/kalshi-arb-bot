"""The execution simulator, rebuilt so it cannot lie about a post-only order.

WHY THIS FILE EXISTS. The CI15M-1 dry run reported a clean
OPEN -> EXTEND -> EXTEND -> COMPLEMENT -> LOCK walk, and the live probe then
submitted twelve orders and filled NONE. The stub filled anything at its
limit; the venue cancels a post-only order that would cross. A simulator
that fills an order the venue would refuse is worse than no simulator -- it
manufactures confidence in a path that cannot work.

THE SEMANTICS, from this venue's own recorded behaviour:

  maker-or-cancel AND the price would cross   -> CANCELLED, immediately.
      Order 145828836710181412: request price 0.70 against ask 0.70,
      makerOrCancel true; body status "cancelled", cancelledAt 17:04:37.503Z
      against createdAt 17:04:37.509Z. Cancelled in the same millisecond.
  maker-or-cancel AND the price rests         -> RESTING. NOT a fill.
      CI15M-1 slots 2-5 sat in exactly this state.
  a resting order fills                       -> only on a print strictly
      THROUGH our price. A print AT our price is not defensible: queue
      position is unknown, and the Gemini fill methodology counts THROUGH.
  no post-only AND the ask is at or below our limit -> FILLED at the ASK,
      capped by displayed size. SEMANTICS-1 proved this live: visible ask
      0.50, marketable limit buy, filled 1 @ 0.50 in 376 ms, AT the limit.
  no post-only AND the ask is above our limit -> no fill. A limit is a
      limit; there is no unrestricted market order anywhere in this
      repository.

DOWN IS SYNTHESIZED. This venue lists one contract per window, the UP side.
A DOWN acquisition costs `1 - bid` and crosses when `1 - bid <= price`,
which is `bid >= 1 - price`.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional

FILLED = "FILLED"
PARTIAL = "PARTIAL"
RESTING = "RESTING"
CANCELLED = "CANCELLED"

ONE = D("1")


def _acquisition(side: str, bid: Optional[D], ask: Optional[D]
                 ) -> Optional[D]:
    """What the venue currently charges to BUY `side`, or None."""
    if side == "UP":
        return ask
    if side == "DOWN":
        return None if bid is None else ONE - bid
    raise ValueError("side must be UP or DOWN, got %r" % (side,))


def _displayed(side: str, bid_size: Optional[D], ask_size: Optional[D]
               ) -> Optional[D]:
    return ask_size if side == "UP" else bid_size


def simulate(*, side: str, price: D, qty: D, maker_or_cancel: bool,
             bid: Optional[D] = None, ask: Optional[D] = None,
             bid_size: Optional[D] = None, ask_size: Optional[D] = None,
             print_through: Optional[D] = None) -> Dict[str, Any]:
    """What this venue would do with that order, given that book.

    `print_through` is a later trade print, used ONLY to resolve a resting
    maker order, and only when it is STRICTLY through our price.
    """
    acq = _acquisition(side, bid, ask)
    out: Dict[str, Any] = {
        "side": side, "limit_price": price, "qty": qty,
        "maker_or_cancel": maker_or_cancel,
        "acquisition_cost": acq,
        "filled_qty": D("0"), "fill_price": None, "fill_basis": None,
    }
    if acq is None:
        out.update(outcome=CANCELLED, would_cross=None,
                   why="the side is not offered; nothing to trade against")
        return out

    # WOULD IT CROSS? Buying crosses when what the venue charges is at or
    # below what we are willing to pay.
    crosses = acq <= price
    out["would_cross"] = crosses

    if maker_or_cancel:
        if crosses:
            out.update(outcome=CANCELLED, why=(
                "post-only (maker-or-cancel) at a crossing price: the "
                "acquisition cost %s is at or below our limit %s, so the "
                "order would take liquidity and the venue cancels it "
                "instead -- in the same millisecond it was created. THIS IS "
                "THE CI15M-1 FAILURE: zero fills were structurally "
                "guaranteed." % (acq, price)))
            return out
        # it rests. RESTING IS NOT A FILL.
        if print_through is not None and print_through < price:
            out.update(outcome=FILLED, filled_qty=qty, fill_price=price,
                       fill_basis="THROUGH", why=(
                           "a print at %s came strictly THROUGH our resting "
                           "%s; price-time priority cannot leave us behind "
                           "it" % (print_through, price)))
            return out
        out.update(outcome=RESTING, why=(
            "the order rests at %s inside the %s/%s book. It is NOT a fill: "
            "a print AT our price is not defensible, because queue position "
            "is unknown." % (price, bid, ask)))
        return out

    # ---- marketable limit (no post-only)
    if not crosses:
        out.update(outcome=CANCELLED, why=(
            "a marketable limit never pays more than its limit: the "
            "acquisition cost %s is above our limit %s, so nothing is "
            "bought. There is no unrestricted market order here."
            % (acq, price)))
        return out
    shown = _displayed(side, bid_size, ask_size)
    got = qty if shown is None else min(qty, D(str(shown)))
    if got <= 0:
        out.update(outcome=CANCELLED, why=(
            "the touch shows no size for our lot, so there is nothing to "
            "take"))
        return out
    out.update(outcome=(FILLED if got >= qty else PARTIAL),
               filled_qty=got, fill_price=acq, fill_basis="TAKER",
               why=("filled %s at the acquisition cost %s, AT the limit %s "
                    "and never above it -- the semantics SEMANTICS-1 proved "
                    "live (ask 0.50, filled 1 @ 0.50 in 376 ms)"
                    % (got, acq, price)))
    return out
