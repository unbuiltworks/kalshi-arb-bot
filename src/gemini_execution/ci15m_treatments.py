"""Two execution treatments over ONE frozen decision engine.

THE POINT OF SEPARATING THEM. CI15M-1 did not choose maker execution -- it
stumbled into it, by pricing off a surface that happened to sit below the
real ask 39.5% of the time. Four fills arrived as a side effect of a bug.
That is not evidence for maker execution and it is not evidence against
taker execution; it is evidence that the price input was wrong.

So the decision and the execution are now explicitly separate. The frozen
policy decides side, size and timing from the REAL ladder, identically in
both arms, and the treatment decides only HOW to reach for it:

  M (maker)      a deliberately NON-CROSSING limit, maker-or-cancel.
                 It is a MAKER EXPERIMENT. It is NOT an "immediately
                 executable acquisition" and must never be described as
                 one: it may rest indefinitely and may never fill.
  T (executable) a CAPPED marketable limit at the real executable ask,
                 makerOrCancel false. Never an uncapped market order --
                 the price is always present and always bounded by the
                 policy's own ceiling.

WHAT THE COMPARISON IS FOR. Whether BTC15m price improvement from resting
compensates for waiting and adverse selection, versus taking the ask
immediately. The BTC5m R2 result (-69pp accuracy given a maker fill) is
external warning evidence about a different population and is deliberately
NOT assumed here.

NEITHER TREATMENT TOUCHES THE POLICY. No threshold, ceiling, side rule,
size or inventory equation is readable from this module, let alone
writable.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional

MAKER = "M"
TAKER = "T"

#: the venue's price increment for these contracts (metadata
#: `priceIncrement`; 0.01 on every BTC15m contract observed).
TICK = D("0.01")


def _acquisition(side: str, bid: D, ask: D) -> D:
    """What the ladder charges to BUY `side`. DOWN is synthesized."""
    if side == "UP":
        return ask
    if side == "DOWN":
        return D("1") - bid
    raise ValueError("side must be UP or DOWN, got %r" % (side,))


def taker_price(side: str, bid: D, ask: D, *, ceiling: D) -> Dict[str, Any]:
    """T -- the capped marketable limit: the real executable acquisition.

    The limit IS the acquisition cost, so the order crosses and fills at
    that price or better and can never pay more. SEMANTICS-1 proved these
    semantics live: visible ask 0.50, marketable limit buy, filled 1 @ 0.50
    in 376 ms -- AT the limit, not above it.
    """
    acq = _acquisition(side, bid, ask)
    if acq > ceiling:
        return {"treatment": TAKER, "ok": False, "price": None,
                "acquisition": str(acq),
                "why": "the executable acquisition %s is above the policy "
                       "ceiling %s" % (acq, ceiling)}
    return {"treatment": TAKER, "ok": True, "price": acq,
            "acquisition": str(acq), "maker_or_cancel": False,
            "crosses": True,
            "why": ("capped marketable limit AT the executable acquisition "
                    "%s, bounded by the ceiling %s. Never an uncapped "
                    "market order." % (acq, ceiling))}


def maker_price(side: str, bid: D, ask: D, *, ceiling: D,
                improve: D = TICK) -> Dict[str, Any]:
    """M -- a deliberately NON-CROSSING limit, derived from the ladder.

    For UP we join the bid side, improving it by one tick when there is
    room inside the spread; for DOWN the synthesized equivalent. The result
    is asserted NOT to cross, because a crossing maker-or-cancel order is
    cancelled on arrival -- which is how CI15M-1 lost eight of twelve.

    THIS IS A MAKER EXPERIMENT. It may rest for the whole window and never
    fill, and a non-fill is a legitimate result rather than a failure.
    """
    acq = _acquisition(side, bid, ask)
    if side == "UP":
        # strictly inside the spread if a tick fits, else join the bid
        px = bid + improve if (bid + improve) < ask else bid
        crosses = px >= ask
    else:
        # DOWN costs 1 - bid; resting means asking for a CHEAPER DOWN,
        # i.e. standing above the current ask on the UP side.
        px = (D("1") - (ask - improve)) if (ask - improve) > bid \
            else (D("1") - ask)
        crosses = px >= (D("1") - bid)
    if px <= 0:
        return {"treatment": MAKER, "ok": False, "price": None,
                "why": "no non-crossing price exists at bid %s ask %s"
                       % (bid, ask)}
    if crosses:
        return {"treatment": MAKER, "ok": False, "price": None,
                "why": ("no room to rest: the derived price %s would cross "
                        "(acquisition %s). A crossing maker-or-cancel order "
                        "is cancelled on arrival." % (px, acq))}
    if px > ceiling:
        return {"treatment": MAKER, "ok": False, "price": None,
                "why": "the resting price %s is above the policy ceiling %s"
                       % (px, ceiling)}
    return {"treatment": MAKER, "ok": True, "price": px,
            "acquisition_if_taken": str(acq),
            "price_improvement_vs_taking": str(acq - px),
            "maker_or_cancel": True, "crosses": False,
            "labels": ("MAKER EXPERIMENT. This is NOT an immediately "
                       "executable acquisition: it rests, it may never "
                       "fill, and a non-fill is a result."),
            "why": ("non-crossing limit %s inside the %s/%s ladder, %s "
                    "better than taking at %s" % (px, bid, ask, acq - px,
                                                  acq))}


def both(side: str, bid: D, ask: D, *, ceiling: D) -> Dict[str, Any]:
    """Both treatments for the SAME policy decision, side by side.

    The strategy decision is already made before this is called; nothing
    here can change side, size or timing.
    """
    m = maker_price(side, bid, ask, ceiling=ceiling)
    t = taker_price(side, bid, ask, ceiling=ceiling)
    return {
        "side": side, "ladder_bid": str(bid), "ladder_ask": str(ask),
        "M": m, "T": t,
        "differ": bool(m.get("ok") and t.get("ok")
                       and m["price"] != t["price"]),
        "improvement": (str(t["price"] - m["price"])
                        if (m.get("ok") and t.get("ok")) else None),
        "labels": ("one decision, two execution treatments. M may never "
                   "fill; T fills or is refused. Neither is assumed better."),
    }
