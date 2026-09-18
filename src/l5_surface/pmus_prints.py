"""Turn the PMUS authenticated trade tape into evidence the ladder accepts.

THE BLOCKER THIS ADDRESSES. PMUS publishes no print tape at all: /trades,
/history, /ohlc, /price-history and /v1/trades?marketSlug were all re-verified
404 on 2026-09-17 against a live slug. The gateway serves `sharesTraded`, a
CUMULATIVE PER-MARKET counter -- it says trades happened somewhere in the
market, never at our price, which is exactly what VOLUME_AHEAD_CLEARED needs.
So the PMUS lane stops at BOOK_REACHED and cannot reach EVIDENCED_FILL.

The authenticated WS (wss://api.polymarket.us/v1/ws/markets) does carry a real
trade tape, and src.pmus_ws_evidence_recorder already reads it READ-ONLY. What
was missing was the join: its rows are {t, px, qty, trade_time, dir} and the
evidence ladder consumes {t, price, size}. This is that join, and nothing more
-- it holds no credential, opens no socket and subscribes to nothing.

COVERAGE IS THE HARD PART, NOT THE RENAME. `coverage_complete` is what turns
silence into an evidenced NO-FILL rather than an unknown, so it must be earned:

  the recorder keeps only the last 500 trades per market. Once that ring has
  WRAPPED, the tape no longer contains everything that happened in the
  interval, and an empty result no longer means nobody traded. A busy market
  is exactly where it wraps, and exactly where a false no-fill would be most
  confident.

  a subscription that began after the quote was placed cannot vouch for the
  start of the interval, however complete it looks afterwards.

Either condition makes coverage UNVOUCHED, and an unvouched silence stays
UNKNOWN. Nothing here can promote silence into a fill in any case; the flag
can only ever produce a no-fill.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "PMUS_PRINTS_V1"

#: the recorder's per-market trade ring. A tape at this length may have
#: dropped older trades, so it can no longer vouch for an interval.
RING_CAPACITY = 500


def to_prints(trades: Optional[Sequence[Mapping[str, Any]]]
              ) -> Optional[List[Dict[str, Any]]]:
    """Recorder rows -> ladder prints. None in, None out.

    None means "we could not read the tape", which the ladder treats as
    UNKNOWN. Returning [] for an unreadable tape would assert that nobody
    traded, which is a different claim and the one that manufactures no-fills.
    """
    if trades is None:
        return None
    out: List[Dict[str, Any]] = []
    for r in trades:
        if not isinstance(r, Mapping):
            continue
        t = r.get("t", r.get("trade_time"))
        px = r.get("px", r.get("price"))
        qty = r.get("qty", r.get("size"))
        if t is None or px is None or qty is None:
            continue
        try:
            out.append({"t": float(t), "price": str(px), "size": str(qty),
                        "dir": r.get("dir")})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda p: p["t"])
    return out


def coverage(trades: Optional[Sequence[Mapping[str, Any]]],
             *, subscribed_at: Optional[float], placed_at: float,
             observed_until: float, ring_full: Optional[bool] = None,
             served_cap: Optional[int] = None) -> Dict[str, Any]:
    """Can we vouch for having watched the WHOLE interval?

    Returns {complete, why}. `complete` is True only when every doubt is
    ruled out; True is a claim about our own observation, not about the venue.

    TWO WAYS THE TAPE CAN BE SHORT, AND COUNTING ROWS ONLY CATCHES ONE. The
    length test below works when the caller holds the recorder's own ring.
    A caller reading the ring over HTTP does not: the recorder serves the
    newest `TRADES_PER_MARKET` (200) of a 500-deep ring, so a wrapped ring
    arrives as 200 rows and `len(trades) >= RING_CAPACITY` can never fire.
    Both truncations therefore travel as explicit flags:

      ring_full   the recorder's ring wrapped -- older trades in the interval
                  were dropped before they were ever served.
      served_cap  the transport served a capped TAIL, so hitting the cap means
                  the view itself may be truncated even on an unwrapped ring.

    Either one makes coverage UNVOUCHED. Passing neither preserves the
    in-process behaviour exactly.
    """
    if trades is None:
        return {"complete": None, "why": "the trade tape could not be read"}
    if subscribed_at is None:
        return {"complete": None,
                "why": ("the subscription's start is unknown, so the "
                        "beginning of the interval cannot be vouched for")}
    if subscribed_at > placed_at:
        return {"complete": None,
                "why": ("the subscription began %.1fs AFTER the quote was "
                        "placed; the start of the interval was not watched"
                        % (subscribed_at - placed_at))}
    if ring_full:
        return {"complete": None,
                "why": ("the recorder reported its trade ring FULL, so older "
                        "trades in this interval were dropped before being "
                        "served. Row count cannot detect this over a capped "
                        "transport, which is why the flag travels with the "
                        "data.")}
    if served_cap is not None and len(trades) >= int(served_cap):
        return {"complete": None,
                "why": ("the tape arrived as a capped tail of %d rows and is "
                        "full at that cap, so it may be truncated: an empty "
                        "or short result no longer covers the interval."
                        % int(served_cap))}
    if len(trades) >= RING_CAPACITY:
        return {"complete": None,
                "why": ("the recorder's %d-trade ring is full, so older "
                        "trades in this interval may have been dropped. An "
                        "empty result would no longer mean nobody traded."
                        % RING_CAPACITY)}
    if observed_until < placed_at:
        return {"complete": None,
                "why": "the observation window ends before the quote existed"}
    return {"complete": True,
            "why": ("subscribed %.1fs before placement, ring not full, "
                    "continuous to the horizon"
                    % (placed_at - subscribed_at))}


def trade_flow_for(quote_px: Any, trades: Optional[Sequence[Mapping[str, Any]]],
                   *, side: str = "BUY", placed_at: float) -> Dict[str, Any]:
    """Volume that traded THROUGH our price after placement, per side.

    This is the figure `sharesTraded` structurally cannot give: it is per
    PRICE, not per market. Reported as a measurement, never as a fill --
    clearing the queue is a separate question the ladder asks next.
    """
    ps = to_prints(trades)
    if ps is None:
        return {"readable": False, "traded_through": None,
                "why": "the trade tape could not be read"}
    from decimal import Decimal as D
    q = D(str(quote_px))
    through = [p for p in ps if p["t"] > placed_at
               and ((D(p["price"]) <= q) if side == "BUY"
                    else (D(p["price"]) >= q))]
    return {
        "readable": True,
        "side": side, "quote_px": str(q),
        "prints_after_placement": sum(1 for p in ps if p["t"] > placed_at),
        "prints_through": len(through),
        "traded_through": str(sum((D(p["size"]) for p in through), D("0"))),
        "labels": ("per PRICE, which is what a cumulative per-market counter "
                   "cannot supply. Not a fill: the queue still has to clear."),
    }
