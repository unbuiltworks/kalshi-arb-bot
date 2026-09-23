"""Classify what actually happened to each frozen-model desired order.

`ASSUMPTION_VOLUME_AT_TOUCH` says a quote at the touch filled when the market's
cumulative volume moved while it was live. That is one assumption doing all the
work, and it is the thing standing between this lane and an advancement gate.
This module replaces it with a CLASSIFICATION whose classes say exactly how
much was seen:

  NO_INTERACTION          nothing traded while the quote was live
  TRADE_AT_PRICE          a print occurred AT our price on the side that
                          would have hit us
  TRADE_THROUGH_PRICE     a print occurred through it, so anything resting at
                          our price on that side was passed
  QUEUE_AMBIGUOUS         volume moved, but the evidence cannot say whether it
                          reached our price or our place in the queue
  OBSERVABLE_FILL_PLAUSIBLE
                          a print at or through our price, on the correct
                          aggressor side, with enough size to clear the queue
                          ahead of us as far as the feed reveals it

NONE OF THESE IS A FILL. A fill is an account fact, and only order/account
evidence establishes one; every row carries `is_fill: False` and
`fill_evidence_source: null` until such evidence exists.

WHAT THE PUBLIC GATEWAY CAN AND CANNOT REACH. There is no public trade tape
(`/trades` is 404) and the public book is a ~30 s cache, so prices, sides and
sizes of individual prints are not visible. On public input this classifier can
therefore only ever emit NO_INTERACTION or QUEUE_AMBIGUOUS, and it says so in
its own output rather than letting the richer classes look unmet. The
authenticated WS trade subscription supplies exactly the missing fields, which
is the whole reason that feed matters.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

NO_INTERACTION = "NO_INTERACTION"
TRADE_AT_PRICE = "TRADE_AT_PRICE"
TRADE_THROUGH_PRICE = "TRADE_THROUGH_PRICE"
QUEUE_AMBIGUOUS = "QUEUE_AMBIGUOUS"
FILL_PLAUSIBLE = "OBSERVABLE_FILL_PLAUSIBLE"

CLASSES = (NO_INTERACTION, TRADE_AT_PRICE, TRADE_THROUGH_PRICE,
           QUEUE_AMBIGUOUS, FILL_PLAUSIBLE)

#: classes that a source without per-print price/side/size can never emit
NEEDS_TRADE_TAPE = (TRADE_AT_PRICE, TRADE_THROUGH_PRICE, FILL_PLAUSIBLE)


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def classify(desired: Mapping[str, Any],
             prints: Optional[Sequence[Mapping[str, Any]]],
             volume_moved: bool,
             *, queue_ahead: Optional[D] = None) -> Dict[str, Any]:
    """One desired order against whatever evidence exists for its interval.

    `prints` is the authenticated trade tape slice covering the interval the
    quote was live, or None when no tape exists. `volume_moved` is the weak
    public signal. `queue_ahead` is the size resting at our price ahead of us
    when the feed reveals it.

    A YES join at p is hit by a print at price <= p whose aggressor is the NO
    side; a NO join at p is hit by a print at YES-price >= 1 - p whose
    aggressor is the YES side. That is the same causal rule the Kalshi shadow
    uses, and it is deliberately conservative: a print that merely occurred
    near our price is not interaction.
    """
    px = _d(desired.get("desired_price"))
    side_up = str(desired.get("side", "")).upper() == "UP"
    out: Dict[str, Any] = {
        "desired_price": str(px) if px is not None else None,
        "side": "UP" if side_up else "DOWN",
        "is_fill": False,
        "fill_evidence_source": None,
        "labels": "a classification of OBSERVED interaction; never a fill",
    }
    if prints is None:
        out["evidence_source"] = "public_gateway_volume_counter_only"
        out["classes_unreachable_here"] = list(NEEDS_TRADE_TAPE)
        out["klass"] = QUEUE_AMBIGUOUS if volume_moved else NO_INTERACTION
        out["why"] = ("no trade tape: volume %s, and nothing says whether it "
                      "reached our price or our queue position"
                      % ("moved" if volume_moved else "did not move"))
        return out

    out["evidence_source"] = "authenticated_trade_tape"
    if px is None:
        out["klass"] = NO_INTERACTION
        out["why"] = "no desired price"
        return out

    thresh = px if side_up else (D(1) - px)
    hits: List[dict] = []
    through = False
    for p in prints:
        ppx = _d(p.get("yes_price"))
        if ppx is None:
            continue
        aggressor = str(p.get("taker_side") or p.get("aggressor") or "").lower()
        if side_up:
            if aggressor == "no" and ppx <= thresh:
                hits.append(dict(p))
                if ppx < thresh:
                    through = True
        else:
            if aggressor == "yes" and ppx >= thresh:
                hits.append(dict(p))
                if ppx > thresh:
                    through = True

    if not hits:
        traded_at_all = any(_d(p.get("yes_price")) is not None for p in prints)
        out["klass"] = QUEUE_AMBIGUOUS if (traded_at_all and volume_moved) \
            else NO_INTERACTION
        out["why"] = ("prints exist but none on the aggressor side at or "
                      "through our price" if traded_at_all
                      else "no prints in the interval")
        return out

    size = sum((_d(p.get("size")) or D(0)) for p in hits)
    out["interacting_prints"] = len(hits)
    out["interacting_size"] = str(size)
    out["first_interaction_t"] = hits[0].get("t")
    if through:
        out["klass"] = TRADE_THROUGH_PRICE
        out["why"] = ("a print traded THROUGH our price on the aggressor "
                      "side, so anything resting at our price was passed")
    else:
        out["klass"] = TRADE_AT_PRICE
        out["why"] = "a print traded AT our price on the aggressor side"

    if queue_ahead is not None and size > queue_ahead:
        out["klass"] = FILL_PLAUSIBLE
        out["queue_ahead"] = str(queue_ahead)
        out["why"] = ("interacting size %s exceeds the %s resting ahead of "
                      "us, so a fill is plausible on the evidence -- still "
                      "NOT a fill, which only account evidence establishes"
                      % (size, queue_ahead))
    elif queue_ahead is None:
        out["queue_ahead"] = None
        out["queue_note"] = ("queue position not revealed by this feed, so "
                             "FILL_PLAUSIBLE cannot be reached")
    return out


def summarize(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    counts = {k: 0 for k in CLASSES}
    for r in rows:
        counts[r.get("klass", NO_INTERACTION)] = counts.get(
            r.get("klass", NO_INTERACTION), 0) + 1
    n = len(rows)
    sources = sorted({r.get("evidence_source") for r in rows})
    return {
        "n": n, "counts": counts,
        "fraction": {k: (round(v / n, 3) if n else None)
                     for k, v in counts.items()},
        "evidence_sources": sources,
        "any_actual_fill": False,
        "note": ("no class here is a fill. With a public-only source the "
                 "three tape-dependent classes are structurally unreachable, "
                 "so their zeros mean 'not observable', not 'did not happen'."),
    }
