"""SEGMENTED HABITAT CENSUS: which regimes this venue offers, and where the
fills we actually have landed inside them.

EVIDENCE ONLY -- model B (venue/economic). Nothing here is reachable from the
L5 policy, the pricing, the sizing or any bound, and no number produced here
is permitted to change one. `habitat.py` records a cycle; this aggregates many
cycles into the segments the owner named:

  spread . touch depth . flow . TTE . probability band . resting time .
  repricing frequency . EXTEND/COMPLETE state . adverse movement after a fill

THE REFUSAL THAT DEFINES THIS MODULE: a segment does NOT get a fill rate.
Five fills exist in the whole programme (C4 x2, C5, C6, C8B). Split nine ways
they are noise, and a table of "0/12, 1/9, 0/31" printed as percentages reads
as knowledge. So every dimension reports the CENSUS -- how many cycles the
venue put in each bucket -- and separately WHERE the fills landed, as counts.
A rate appears only once `MIN_FILLS_FOR_A_RATE` fills exist, and until then
each cell carries the reason it is absent.

AND THE FILL COUNT ITSELF IS A FLOOR where a window's terminal read left an
order resting. C5's did: its snapshot said two orders open and zero fills, and
one of them filled 57 seconds later. Counting from the snapshot alone dropped
that fill and, with it, the clearest instance of the resting-time pattern this
module measures.

WHAT EACH NUMBER COMES FROM, and what it is not:

  spread, probability     the PRESENTED book -- the state the controller
                          actually decided on, never a reconstruction
  touch depth             the separate ladder read; our own resting size is
                          FLAGGED, never subtracted
  flow                    adjacent-cycle deltas in the PUBLIC print count.
                          NOT derived from an assumed contract duration: it is
                          measured between two reads with their own clocks
  TTE                     the venue's clock, or the venue's own expiryDate
                          minus the venue's own createdAt for a rung
  resting time            PLACE -> CANCEL from the mutation record, or
                          createdAt -> updatedAt from the venue's order record
  adverse movement        the presented mid after a fill, sampled forward

A REFUSED BOOK IS NOT A NARROW SPREAD. Cycles whose book the controller
refused are counted on their own line and contribute to no price-derived
segment, because the readings that were unusable are exactly the ones that
would report the venue as tighter than it is.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: below this many fills in total, NO segment states a conversion rate.
#: Not a statistical threshold anyone derived -- a floor chosen so that the
#: present evidence (4) cannot produce a percentage.
MIN_FILLS_FOR_A_RATE = 10

#: the request limit `habitat.public_flow` uses. If a read ever returns this
#: many prints the tape was truncated and a delta between two reads is not a
#: rate, so flow is recorded UNREADABLE rather than understated.
FLOW_TAPE_LIMIT = 200

UNREADABLE = "UNREADABLE"
NOT_PRESENTED = "NOT_PRESENTED_AT_DECISION"

SPREAD_EDGES = (D("0.05"), D("0.10"), D("0.20"), D("0.30"))
SPREAD_LABELS = ("<=0.05", "0.06-0.10", "0.11-0.20", "0.21-0.30", ">0.30")

DEPTH_EDGES = (D("1"), D("10"), D("100"), D("500"))
DEPTH_LABELS = ("<=1", "2-10", "11-100", "101-500", ">500")

FLOW_EDGES = (D("0"), D("2"), D("6"), D("15"))
FLOW_LABELS = ("0/min", "0-2/min", "2-6/min", "6-15/min", ">15/min")

TTE_EDGES = (D("60"), D("120"), D("180"), D("240"))
TTE_LABELS = ("<=60s", "60-120s", "120-180s", "180-240s", ">240s")

PROB_EDGES = (D("0.10"), D("0.25"), D("0.50"), D("0.75"), D("0.90"))
PROB_LABELS = ("0.00-0.10", "0.11-0.25", "0.26-0.50", "0.51-0.75",
               "0.76-0.90", "0.91-1.00")

REST_EDGES = (D("1"), D("10"), D("30"), D("60"))
REST_LABELS = ("<=1s", "1-10s", "10-30s", "30-60s", ">60s")

DIMENSIONS = ("spread", "touch_depth", "flow", "tte", "probability_band",
              "l5_state")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _bucket(value: Any, edges: Sequence[D], labels: Sequence[str]) -> str:
    x = _dec(value)
    if x is None:
        return UNREADABLE
    for i, e in enumerate(edges):
        if x <= e:
            return labels[i]
    return labels[-1]


def spread_bucket(x: Any) -> str:
    return _bucket(x, SPREAD_EDGES, SPREAD_LABELS)


def depth_bucket(x: Any) -> str:
    return _bucket(x, DEPTH_EDGES, DEPTH_LABELS)


def flow_bucket(x: Any) -> str:
    return _bucket(x, FLOW_EDGES, FLOW_LABELS)


def tte_bucket(x: Any) -> str:
    return _bucket(x, TTE_EDGES, TTE_LABELS)


def probability_bucket(x: Any) -> str:
    return _bucket(x, PROB_EDGES, PROB_LABELS)


def resting_bucket(x: Any) -> str:
    return _bucket(x, REST_EDGES, REST_LABELS)


# ===================================================================== flow

def flow_rates(cycles: Sequence[Mapping[str, Any]]) -> List[Optional[str]]:
    """Prints per minute, MEASURED between adjacent reads of one symbol.

    The alternative -- dividing a cumulative print count by an assumed
    five-minute contract life -- would bake a duration this module has no
    business asserting into every flow number. Two reads with their own
    timestamps need no such assumption.

    The first cycle of a symbol has no predecessor and therefore no rate. That
    is `None`, which is not zero flow.
    """
    out: List[Optional[str]] = []
    prev: Dict[str, Any] = {}
    for row in cycles:
        sym = row.get("symbol")
        f = (row.get("habitat") or {}).get("flow") or {}
        n, t = f.get("n_prints"), _epoch(row.get("utc"))
        p = prev.get(sym)
        prev[sym] = (n, t)
        if p is None or not f.get("readable"):
            out.append(None)
            continue
        pn, pt = p
        if (not isinstance(n, int) or not isinstance(pn, int)
                or t is None or pt is None or t <= pt):
            out.append(None)
            continue
        if n >= FLOW_TAPE_LIMIT or pn >= FLOW_TAPE_LIMIT:
            out.append(None)                       # truncated tape, not a rate
            continue
        if n < pn:
            out.append(None)                       # count went backwards
            continue
        out.append(str(round(D(n - pn) * D("60") / D(str(t - pt)), 3)))
    return out


def _epoch(utc: Any) -> Optional[float]:
    from datetime import datetime
    if not isinstance(utc, str):
        return None
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ================================================================== a cycle

def cycle_segments(row: Mapping[str, Any], *,
                   flow_rate: Any = None) -> Dict[str, Any]:
    """One cycle placed in every dimension it can honestly be placed in."""
    hb = row.get("habitat") or {}
    readable = bool(hb.get("book_readable"))
    lad = hb.get("ladder") or {}
    bids = lad.get("bids") or {}
    acts = row.get("l5_action") or []
    states = sorted({str(a.get("action")) for a in acts
                     if isinstance(a, Mapping) and a.get("action")})
    out: Dict[str, Any] = {
        "utc": row.get("utc"), "symbol": row.get("symbol"),
        "book_readable": readable,
        "book_why": hb.get("book_why"),
        "tte": tte_bucket(row.get("tte_s") or hb.get("tte_s")),
        "flow": flow_bucket(flow_rate) if flow_rate is not None else UNREADABLE,
        "l5_state": ("+".join(states) if states else "NO_ACTION"),
        "touch_is_ours": bool(bids.get("touch_is_ours")),
    }
    # PRICE-DERIVED DIMENSIONS ONLY FROM A BOOK THE CONTROLLER ACCEPTED
    if readable:
        out["spread"] = spread_bucket(hb.get("yes_spread"))
        out["probability_band"] = probability_bucket(hb.get("yes_bid"))
        out["touch_depth"] = (depth_bucket(bids.get("best_qty"))
                              if lad.get("readable") else UNREADABLE)
    else:
        out["spread"] = out["probability_band"] = out["touch_depth"] = UNREADABLE
    return out


# ================================================================== rungs

def rung_intervals(mutations: Sequence[Mapping[str, Any]], *,
                   window_end: Any = None,
                   filled_prices: Sequence[str] = (),
                   filled_utc: Optional[Mapping[str, str]] = None
                   ) -> List[Dict[str, Any]]:
    """PLACE -> CANCEL per rung, with how many times each leg was repriced.

    A rung still live at the window's end is recorded with `ended: "AT_STOP"`,
    because "we stopped watching" is not "it was cancelled" and its resting
    seconds are a floor rather than a measurement.

    A REPRICE IS A CANCEL THAT WAS FOLLOWED BY ANOTHER PLACE ON THAT LEG. The
    terminal cancel that withdraws a rung at the end of a window is a
    withdrawal, not a reprice, and counting it as one made C7's YES rung --
    placed once and left alone for 72 s -- read as "repriced".
    """
    places: Dict[str, int] = {}
    for m in mutations or []:
        if m.get("kind") == "PLACE":
            leg = str((m.get("target") or {}).get("leg") or "")
            places[leg] = places.get(leg, 0) + 1

    live: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for m in mutations or []:
        t = m.get("target") or {}
        leg, px = str(t.get("leg") or ""), str(t.get("price") or "")
        ts = _epoch(m.get("utc"))
        if m.get("kind") == "PLACE":
            live[leg] = {"leg": leg, "price": px, "placed_utc": m.get("utc"),
                         "placed_t": ts, "action": t.get("action")}
        elif m.get("kind") == "CANCEL" and leg in live:
            r = live.pop(leg)
            out.append({**r, "ended_utc": m.get("utc"), "ended": "CANCEL",
                        "resting_s": (round(ts - r["placed_t"], 3)
                                      if ts and r["placed_t"] else None)})
    # A RUNG THAT FILLED STOPPED RESTING WHEN IT FILLED, not when we stopped
    # watching. Both of the two fills on record were still "live" at the
    # window's end, so measuring them to the stop overstated the only two
    # resting times that have a fill attached to them.
    end = _epoch(window_end)
    ffills = dict(filled_utc or {})
    for leg, r in live.items():
        at = ffills.get(r["price"])
        stop_t = _epoch(at) if at else end
        out.append({**r, "ended_utc": (at or window_end),
                    "ended": ("FILL" if at else "AT_STOP"),
                    "resting_s": (round(stop_t - r["placed_t"], 3)
                                  if stop_t and r["placed_t"] else None)})
    filled = {str(p) for p in (filled_prices or [])}
    for r in out:
        r.pop("placed_t", None)
        r["filled"] = r["price"] in filled
        r["places_on_this_leg"] = places.get(r["leg"], 0)
        r["reprices_on_this_leg"] = max(places.get(r["leg"], 0) - 1, 0)
        r["resting_bucket"] = resting_bucket(r.get("resting_s"))
    return out


# ======================================================= adverse movement

def adverse_movement(*, leg: str, fill_price: Any,
                     later: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Where the presented mid went after a fill, sampled forward.

    MARK-TO-MARKET, NOT REALIZED. A mid is a measurement of a market; the
    position settled at 0 or 1 and only that settlement is money.
    """
    px = _dec(fill_price)
    out: Dict[str, Any] = {
        "leg": leg, "fill_price": (str(px) if px is not None else None),
        "samples": [],
        "labels": ("mark-to-market on the PRESENTED mid. Not realized, not a "
                   "P&L line, and never summed with matched-pair economics."),
    }
    if px is None:
        out["readable"] = False
        out["why"] = "the fill price was not readable"
        return out
    for row in later or []:
        hb = row.get("habitat") or row
        if not hb.get("book_readable"):
            out["samples"].append({"utc": row.get("utc"), "mid": None,
                                   "why": "book refused at this cycle"})
            continue
        side = "yes" if str(leg).upper() == "YES" else "no"
        bid = _dec(hb.get("%s_bid" % side))
        ask = _dec(hb.get("%s_ask" % side))
        if bid is None or ask is None:
            out["samples"].append({"utc": row.get("utc"), "mid": None,
                                   "why": "one side of the book was absent"})
            continue
        mid = (bid + ask) / D("2")
        out["samples"].append({"utc": row.get("utc"), "mid": str(mid),
                               "vs_fill": str(mid - px)})
    moved = [_dec(s.get("vs_fill")) for s in out["samples"]]
    moved = [m for m in moved if m is not None]
    out["readable"] = bool(moved)
    if moved:
        out["worst_against"] = str(min(moved))
        out["best_for"] = str(max(moved))
        out["last"] = str(moved[-1])
    else:
        out["why"] = "no cycle after the fill presented a readable book"
    return out


# ================================================================== census

def census(cycles: Sequence[Mapping[str, Any]], *,
           fill_segments: Sequence[Mapping[str, Any]] = (),
           rungs: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """The whole census: cycles per bucket, fills per bucket, no rates."""
    rates = flow_rates(cycles)
    segs = [cycle_segments(c, flow_rate=r) for c, r in zip(cycles, rates)]
    n = len(segs)
    refused = [s for s in segs if not s["book_readable"]]
    n_fills = len(fill_segments or [])

    why_no_rate = (
        "NO CONVERSION RATE IS STATED. %d fill%s exist in the whole "
        "programme and %d are needed before a per-segment rate is anything "
        "but noise. The census below is what the venue OFFERED; the fill "
        "column is where the fills we have landed, as counts."
        % (n_fills, "" if n_fills == 1 else "s", MIN_FILLS_FOR_A_RATE))

    dims: Dict[str, Any] = {}
    for dim in DIMENSIONS:
        counts: Dict[str, int] = {}
        for s in segs:
            counts[s[dim]] = counts.get(s[dim], 0) + 1
        fcounts: Dict[str, int] = {}
        for f in (fill_segments or []):
            b = f.get(dim, NOT_PRESENTED)
            fcounts[b] = fcounts.get(b, 0) + 1
        dims[dim] = {
            b: {"cycles": c,
                "share": (round(c / n, 3) if n else None),
                "fills": fcounts.get(b, 0),
                "rate": None,
                "rate_why": why_no_rate}
            for b, c in sorted(counts.items())}
        # a bucket the fills landed in that no cycle census covers (a fill
        # whose decision state was never recorded) is still reported
        for b, c in sorted(fcounts.items()):
            if b not in dims[dim]:
                dims[dim][b] = {"cycles": 0, "share": None, "fills": c,
                                "rate": None, "rate_why": why_no_rate}

    rest: Dict[str, Any] = {}
    for r in rungs or []:
        b = r.get("resting_bucket") or UNREADABLE
        cell = rest.setdefault(b, {"rungs": 0, "fills": 0})
        cell["rungs"] += 1
        cell["fills"] += 1 if r.get("filled") else 0
    repriced = [r for r in (rungs or []) if r.get("reprices_on_this_leg")]
    never = [r for r in (rungs or []) if not r.get("reprices_on_this_leg")]

    return {
        "cycles": n,
        "book_refused_cycles": len(refused),
        "book_refused_share": (round(len(refused) / n, 3) if n else None),
        "refusal_reasons": sorted({str(s.get("book_why")) for s in refused
                                   if s.get("book_why")})[:6],
        "touch_was_ours_cycles": sum(1 for s in segs if s["touch_is_ours"]),
        "fills_total": n_fills,
        "rates_stated": False,
        "why_no_rates": why_no_rate,
        "dimensions": dims,
        "resting_time": rest,
        "repricing": {
            "rungs_total": len(rungs or []),
            "rungs_on_a_repriced_leg": len(repriced),
            "fills_on_a_repriced_leg": sum(1 for r in repriced
                                           if r.get("filled")),
            "rungs_never_repriced": len(never),
            "fills_never_repriced": sum(1 for r in never if r.get("filled")),
            "caution": ("ASSOCIATION, NOT CAUSE, and confounded on its face: "
                        "the leg that gets repriced is the leg whose price is "
                        "moving, so 'repriced rungs do not fill' and 'the "
                        "moving leg does not fill' are the same observation "
                        "here. Nothing in the controller may be changed on "
                        "this."),
        },
        "labels": ("SEGMENTED HABITAT CENSUS -- model B evidence. Not a "
                   "verdict, not a tuning input, not realized economics, and "
                   "not reachable from the L5 policy."),
    }
