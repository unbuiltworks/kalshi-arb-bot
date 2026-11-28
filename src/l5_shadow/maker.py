"""Hypothetical MAKER placement -- and the evidence test it must pass.

THE RULE THIS FILE EXISTS TO ENFORCE: a hypothetical maker fill is not counted
without subsequent observable evidence. Resting is free to imagine and that is
exactly the problem -- a shadow that credits itself a maker fill whenever the
price later traded through will report an execution quality no real queue ever
delivers, and on both venues the maker side is where the economics live
(Polymarket declares `takerOnly: true`, so its maker pays no fee at all).

WHAT WE CAN SEE AND WHAT WE CANNOT. A public book shows the SIZE resting at a
price, never our position in it. So the honest test of "would our quote have
filled" is a QUEUE test against later public prints:

    queue_ahead   size already resting at our price when we would have joined.
                  We join BEHIND all of it.
    traded_thru   cumulative size of later public prints that would have hit
                  that price, i.e. prints at a price that crosses our quote.
    filled_qty    max(0, traded_thru - queue_ahead), capped at our size.

`evidenced_fill` is True only when that arithmetic clears our whole quote from
observed prints. With no prints observed the answer is NOT False -- it is
`None` with a reason, because "nobody traded yet" and "we would not have
filled" are different facts and only the second is a finding.

CANCELS, REPLACEMENTS AND HIDDEN SIZE ARE NOT MODELLED, all of which would make
a real queue drain FASTER than this test allows. The test is therefore
CONSERVATIVE about filling, which is the correct direction: it under-reports
maker fills rather than inventing them.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

# a print at a price that would have traded against a resting BUY at `px`
# must be at or below `px`: someone sold into it.
def _crosses_buy(print_px: D, quote_px: D) -> bool:
    return print_px <= quote_px


def queue_ahead(levels: Sequence, px: D) -> D:
    """Size resting AT our price already. We join behind it, never in front."""
    tot = D("0")
    for p, s in levels:
        if D(str(p)) == D(str(px)):
            tot += D(str(s))
    return tot


def join_price(levels: Sequence, *, tick: D, ceiling: D,
               improve: bool = False) -> Optional[D]:
    """Where a maker BUY would rest: at the best bid, or one tick inside it.

    `levels` is the BID ladder, best-first. Joining the touch is the
    conservative default -- improving it jumps the queue in the model, which is
    precisely the sort of free advantage this module refuses to grant itself
    unless asked for explicitly.
    """
    if not levels:
        return None
    best = D(str(levels[0][0]))
    px = (best + D(str(tick))) if improve else best
    return px if px <= D(str(ceiling)) else None


def fill_criteria(*, quote_px: D, quote_qty: D, ahead: D,
                  later: Sequence[dict]) -> Dict[str, Any]:
    """Four fill criteria of increasing strength, scored SEPARATELY.

    A trade merely touching our limit is not evidence that WE filled. For a
    resting BUY at P:

      TOUCH                 some print happened at or below P. Says the price
                            was reached. Says nothing about our queue position,
                            and it is the criterion that would let a deep quote
                            look profitable on liquidity it never had.
      TRADE_AT_PRICE        volume actually executed AT P. Someone sold into
                            the level we were resting in -- but possibly only
                            into the orders ahead of us.
      TRADE_THROUGH         a print occurred STRICTLY BELOW P. A seller would
                            hit the better bid first, so the level at P must
                            have been exhausted to reach it. Direct evidence
                            the level cleared.
      VOLUME_AHEAD_CLEARED  cumulative volume at or below P exceeds the size
                            resting ahead of us AND covers our own quantity.
                            The queue-accounted criterion, and the strictest.

    Live-readiness uses the strongest supportable one. Reporting a weaker one
    as a fill is how arm D would look profitable on queue priority we did not
    possess.
    """
    at = [p for p in later if D(str(p["price"])) == quote_px]
    thru = [p for p in later if D(str(p["price"])) < quote_px]
    v_at = sum((D(str(p["size"])) for p in at), D("0"))
    v_thru = sum((D(str(p["size"])) for p in thru), D("0"))
    v_all = v_at + v_thru
    got = min(max(D("0"), v_all - ahead), quote_qty)
    return {
        "TOUCH": bool(later),
        "TRADE_AT_PRICE": v_at > 0,
        "TRADE_THROUGH": v_thru > 0,
        "VOLUME_AHEAD_CLEARED": bool(quote_qty > 0 and got >= quote_qty),
        "volume_at_price": str(v_at),
        "volume_through": str(v_thru),
        "volume_total": str(v_all),
        "queue_ahead": str(ahead),
        "cleared_for_us": str(got),
        "strongest_satisfied": ("VOLUME_AHEAD_CLEARED"
                                if (quote_qty > 0 and got >= quote_qty)
                                else "TRADE_THROUGH" if v_thru > 0
                                else "TRADE_AT_PRICE" if v_at > 0
                                else "TOUCH" if later else "NONE"),
        "labels": ("increasing strength. A fill is counted at "
                   "VOLUME_AHEAD_CLEARED only; the weaker rows are reported so "
                   "the difference between them is visible, never so a weaker "
                   "one can be used as a fill"),
    }


def evidence_for_quote(*, quote_px: Any, quote_qty: Any, ahead: Any,
                       placed_at: float, prints: Optional[Sequence[dict]],
                       observed_until: float,
                       coverage_complete: Optional[bool] = None
                       ) -> Dict[str, Any]:
    """Would this resting BUY have filled, per LATER public prints only?

    Prints at or before `placed_at` are excluded: a trade that already happened
    cannot fill an order that did not yet exist. That is the same lookahead
    discipline the prediction store enforces, applied to execution.

    `coverage_complete` IS WHAT TURNS SILENCE INTO EVIDENCE. "Nobody traded"
    and "we would not have filled" are different facts, and only the second is
    a finding -- unless we can vouch for having watched the WHOLE interval. The
    Gemini socket can: `prints_since` returns None rather than an empty list
    whenever its continuous view began after the quote, so a non-None empty
    result means we watched from placement to horizon and nothing crossed.
    That is an evidenced NO-FILL, and calling it UNKNOWN discards real
    evidence -- which is why 230 of 245 Gemini verdicts were undecided.

    The caller must PROVE coverage; the default is None, and None is unknown.
    Nothing here can promote silence into a FILL, in any case: this flag can
    only ever produce a no-fill.
    """
    q_px, q_qty, ah = D(str(quote_px)), D(str(quote_qty)), D(str(ahead))
    if prints is None:
        return {"evidenced_fill": None, "reason":
                "the venue's public prints could not be read: UNKNOWN, not a "
                "no-fill", "queue_ahead": str(ah), "traded_through": None,
                "observed_window_s": round(observed_until - placed_at, 3)}
    later = [p for p in prints if float(p.get("t") or 0) > placed_at
             and _crosses_buy(D(str(p["price"])), q_px)]
    thru = sum((D(str(p["size"])) for p in later), D("0"))
    got = max(D("0"), thru - ah)
    got = min(got, q_qty)
    full = got >= q_qty
    if not later:
        window = round(observed_until - placed_at, 1)
        if coverage_complete is True:
            return {"evidenced_fill": False, "reason":
                    (f"no public print at or below {q_px} in the {window}s "
                     f"after placement, over an interval we can vouch for "
                     f"CONTINUOUSLY: an evidenced no-fill, not an unknown"),
                    "queue_ahead": str(ah), "traded_through": "0",
                    "modelled_fill_qty": "0", "prints_considered": 0,
                    "coverage_complete": True,
                    "fill_criteria": fill_criteria(
                        quote_px=q_px, quote_qty=q_qty, ahead=ah, later=[]),
                    "observed_window_s": round(observed_until - placed_at, 3)}
        return {"evidenced_fill": None, "reason":
                (f"no public print at or below {q_px} in the "
                 f"{window}s observed after "
                 f"placement, and the interval is NOT vouched for; not "
                 f"decided, and NOT a no-fill"),
                "queue_ahead": str(ah), "traded_through": "0",
                "modelled_fill_qty": "0", "prints_considered": 0,
                "coverage_complete": coverage_complete,
                "observed_window_s": round(observed_until - placed_at, 3)}
    return {
        "evidenced_fill": bool(full),
        "fill_criteria": fill_criteria(quote_px=q_px, quote_qty=q_qty,
                                       ahead=ah, later=later),
        "reason": (f"{thru} traded through {q_px} after placement against a "
                   f"queue of {ah} ahead; that clears "
                   f"{got} of our {q_qty}"),
        "queue_ahead": str(ah),
        "traded_through": str(thru),
        "modelled_fill_qty": str(got),
        "prints_considered": len(later),
        "coverage_complete": coverage_complete,
        "first_cross_t": min(float(p["t"]) for p in later),
        "time_to_fill_s": (round(min(float(p["t"]) for p in later) - placed_at, 3)
                           if full else None),
        "evidence": [{"t": float(p["t"]), "price": str(p["price"]),
                      "size": str(p["size"]), "side": p.get("side"),
                      "venue": p.get("venue")} for p in later[:20]],
        "observed_window_s": round(observed_until - placed_at, 3),
        "labels": ("queue-conservative: we join behind all resting size and "
                   "cancels/hidden size are not modelled, both of which would "
                   "only make a real queue drain faster. This UNDER-reports "
                   "maker fills by construction."),
    }
