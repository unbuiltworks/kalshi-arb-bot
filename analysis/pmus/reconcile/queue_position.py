"""Is there a defensible place to rest behind the dominant PMUS quote?

ONE QUESTION ONLY: at the touch and at 1/2/3/5 ticks behind it, how big is the
queue, how much opposing volume actually reaches that price, and does resting
deeper buy a better combination of fill chance, price and residual risk? This
is not a policy sweep.

THE COLLECTOR USED, NOT A NEW ONE. `src/pmus_book_tape.py` wrote
`data/s3_cohort/pmus_btcupdown15m_20261006T232125Z.jsonl`: 2-second polling,
21 BTC15m windows, 6,546 full-depth snapshots of ~36 levels a side, plus `bbo`
rows carrying cumulative `shares_traded` and `last`. Nothing new was built.

PMUS SINGLE-BOOK UP/DOWN SEMANTICS. One book per contract, quoted on the UP
side. An UP buyer rests on the `bids` ladder. A DOWN buyer paying d rests
where UP sells at 1-d, so the DOWN queue at own-side price d is the `offers`
size at 1-d. Both sides are measured that way; there is no second book.

WHAT BBO SNAPSHOTS AND CUMULATIVE VOLUME CAN AND CANNOT DECIDE

  CAN: whether a size decrease at a level coincided with printed volume.
       A decrease with volume >= the decrease is CONSISTENT_WITH_TRADE.
       A decrease with no volume at all is CANCEL_NO_VOLUME -- an order
       pulled, not consumed. A size that returns to its prior level with no
       intervening volume is REPLACED_NO_TRADE.
  CANNOT: the identity or arrival order of individual orders, and therefore
       our own FIFO position. Two orders of 250 replacing one of 500 is
       indistinguishable from one order of 500 persisting if both snapshots
       read 500. Anything inside a 2-second gap is unordered.

So nothing here claims a fill. The deepest claim available from this data is
that volume did or did not print at a price while a stated size stood there.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

TICK = D("0.01")
OFFSETS = (0, 1, 2, 3, 5)


def _d(x) -> Optional[D]:
    try:
        return None if x is None else D(str(x))
    except Exception:                                          # noqa: BLE001
        return None


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:                                          # noqa: BLE001
        return None


def _ts(s) -> Optional[float]:
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(s)).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 2)


def load(path: str):
    bbo: Dict[str, List[dict]] = defaultdict(list)
    book: Dict[str, List[dict]] = defaultdict(list)
    errs: Dict[str, int] = defaultdict(int)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        slug, kind, t = r.get("slug"), r.get("kind"), _ts(r.get("t"))
        if not slug or t is None:
            continue
        r["_t"] = t
        if kind == "bbo":
            bbo[slug].append(r)
        elif kind == "book":
            book[slug].append(r)
        elif kind in ("error", "book_error"):
            errs[slug] += 1
    for m in (bbo, book):
        for v in m.values():
            v.sort(key=lambda r: r["_t"])
    return bbo, book, errs


def ladder(rows: Sequence[Sequence[str]]) -> Dict[D, float]:
    out: Dict[D, float] = {}
    for lv in rows or []:
        try:
            p, q = D(str(lv[0])), float(lv[1])
        except Exception:                                      # noqa: BLE001
            continue
        out[p] = q
    return out


def quote_dynamics(bbo: Sequence[dict], book: Sequence[dict]) -> dict:
    """Persistent queue, replenishment, or cancel/replace?

    THE CORRECTION THIS EMBODIES. A first version read `bid_shares` from the
    bbo rows, which is the AGGREGATE of every bid level -- so "unchanged"
    meant the whole book's depth was unchanged, which is not the question.
    The touch size now comes from the book ladder's own top level.

    Each consecutive pair of book snapshots at the SAME touch price is
    classified against the volume printed between them:

      PERSISTENT          size unchanged, no volume printed. Nothing happened.
      REPLENISHED         size unchanged BUT volume printed at or below the
                          touch. Something was consumed and the level was
                          refilled to the same size inside 2 s. A quote
                          behind it gains no ground.
      CONSUMED            size fell and volume printed at least the fall.
      CANCELLED           size fell with NO volume at all. An order pulled.
      ADDED               size rose.

    REPLENISHED versus CONSUMED-then-refilled cannot be separated further:
    both look identical across a 2-second gap, and neither reveals whether
    the refill is the same participant. That limit is the point of item 7.
    """
    byt = sorted(book, key=lambda r: r["_t"])
    vols = [(r["_t"], _f(r.get("shares_traded")), _d(r.get("last")))
            for r in bbo]

    def printed(t0: float, t1: float, lvl: D) -> float:
        """Volume printing at or below lvl strictly inside (t0, t1]."""
        tot = 0.0
        prev = None
        for t, v, px in vols:
            if v is None:
                continue
            if prev is not None and t0 < t <= t1 and v > prev \
                    and px is not None and px <= lvl:
                tot += v - prev
            prev = v
        return tot

    cls: Dict[str, int] = defaultdict(int)
    prev_b = None
    for b in byt:
        bids = ladder(b.get("bids"))
        if not bids:
            prev_b = None
            continue
        touch = max(bids)
        sz = bids[touch]
        if prev_b is not None and prev_b[0] == touch:
            v = printed(prev_b[2], b["_t"], touch)
            if sz > prev_b[1]:
                cls["ADDED"] += 1
            elif sz < prev_b[1]:
                cls["CONSUMED" if v >= (prev_b[1] - sz) - 1e-6
                    else ("CANCELLED" if v <= 0 else
                          "DECREASE_PARTIALLY_EXPLAINED")] += 1
            else:
                cls["REPLENISHED" if v > 0 else "PERSISTENT"] += 1
        elif prev_b is not None:
            cls["TOUCH_PRICE_MOVED"] += 1
        prev_b = (touch, sz, b["_t"])
    tot = sum(cls.values()) or 1
    same = cls.get("PERSISTENT", 0) + cls.get("REPLENISHED", 0)
    return {
        "transitions": sum(cls.values()),
        "classes": dict(cls),
        "replenished_share_of_same_price_unchanged_size": (
            round(cls.get("REPLENISHED", 0) / same, 4) if same else None),
        "DETERMINABLE": ("whether a size change coincided with printed "
                         "volume at or below the touch"),
        "NOT_DETERMINABLE": ("order identity, FIFO position, and whether a "
                             "refill is the same participant; anything "
                             "inside a 2 s gap is unordered"),
    }


def level_opportunity(bbo: Sequence[dict], book: Sequence[dict], *,
                      horizon_s: float = 180.0,
                      stride: int = 10) -> dict:
    """Queue at each candidate level, and the opposing volume that printed
    at or through it AFTER a quote was placed there.

    THE CORRECTION THIS EMBODIES. A first version of this function compared
    each snapshot's queue against the WHOLE WINDOW's volume, which produced
    clearance ratios of 90-170x and meant nothing: volume that printed before
    a quote existed cannot clear its queue. Placement is now a point in time,
    and only volume in the `horizon_s` that FOLLOWS it counts, measured
    against the queue standing at the moment of placement.

    Still not a fill. Clearing the queue is necessary and not sufficient: we
    cannot see our own FIFO position, and `last`-price attribution is right
    about 73% of the time where it commits.
    """
    prints: List[tuple] = []
    prev = None
    for r in bbo:
        v, px, t = _f(r.get("shares_traded")), _d(r.get("last")), r["_t"]
        if prev is not None and v is not None and prev > 0 and v > prev \
                and px is not None:
            prints.append((t, px, v - prev))
        if v is not None:
            prev = v

    def vol_through(t0: float, lvl: D, up: bool) -> float:
        return sum(q for t, p, q in prints
                   if t0 < t <= t0 + horizon_s
                   and (p <= lvl if up else p >= lvl))

    out: Dict[str, Any] = {}
    for side in ("UP", "DOWN"):
        rows: Dict[int, Dict[str, List[float]]] = {
            k: {"queue": [], "reach": [], "cleared": []} for k in OFFSETS}
        for b in book[::stride]:
            bids, offers = ladder(b.get("bids")), ladder(b.get("offers"))
            if not bids or not offers:
                continue
            t0 = b["_t"]
            touch = max(bids) if side == "UP" else min(offers)
            for k in OFFSETS:
                if side == "UP":
                    lvl = touch - k * TICK
                    q = bids.get(lvl)
                else:
                    lvl = touch + k * TICK
                    q = offers.get(lvl)
                qq = 0.0 if q is None else q
                v = vol_through(t0, lvl, side == "UP")
                rows[k]["queue"].append(qq)
                rows[k]["reach"].append(v)
                rows[k]["cleared"].append(1.0 if v > qq else 0.0)
        out[side] = {"ticks_behind_touch": {
            str(k): {
                "placements": len(v["queue"]),
                "queue_at_placement_p50": _pct(v["queue"], 0.5),
                "queue_at_placement_p90": _pct(v["queue"], 0.9),
                "share_level_empty_at_placement": (
                    round(sum(1 for x in v["queue"] if x == 0)
                          / len(v["queue"]), 4) if v["queue"] else None),
                "volume_through_in_horizon_p50": _pct(v["reach"], 0.5),
                "share_queue_cleared_in_horizon": (
                    round(sum(v["cleared"]) / len(v["cleared"]), 4)
                    if v["cleared"] else None),
            } for k, v in rows.items()}}
    out["horizon_s"] = horizon_s
    out["placement_stride_snapshots"] = stride
    out["prints_intervals"] = len(prints)
    out["CLEARED_MEANING"] = (
        "volume printing at or through the level in the horizon AFTER "
        "placement exceeded the queue standing there at placement. "
        "NECESSARY for a fill, NOT sufficient: FIFO position is unobservable")
    return out


def five_hundred_check(bbo: Sequence[dict], book: Sequence[dict]) -> dict:
    """Does the 500x500 pattern from the Aug 30-Sep 2 BBO tape appear here?"""
    pairs = 0
    both500 = 0
    tsz: List[float] = []
    for b in book:
        bids, offers = ladder(b.get("bids")), ladder(b.get("offers"))
        if not bids or not offers:
            continue
        pairs += 1
        bq, aq = bids[max(bids)], offers[min(offers)]
        tsz.append(bq)
        if abs(bq - 500.0) < 1e-9 and abs(aq - 500.0) < 1e-9:
            both500 += 1
    return {"book_snapshots": pairs,
            "touch_500x500": both500,
            "share_500x500": (round(both500 / pairs, 4) if pairs else None),
            "touch_bid_size_p10": _pct(tsz, 0.1),
            "touch_bid_size_p50": _pct(tsz, 0.5),
            "touch_bid_size_p90": _pct(tsz, 0.9)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/s3_cohort/"
                                      "pmus_btcupdown15m_20261006T232125Z"
                                      ".jsonl")
    ap.add_argument("--min-books", type=int, default=100)
    ap.add_argument("--out",
                    default="analysis/pmus/reconcile/queue_position.json")
    a = ap.parse_args(argv)

    bbo, book, errs = load(a.tape)
    windows = [s for s in book if len(book[s]) >= a.min_books]
    rep: Dict[str, Any] = {
        "VENUE": "PMUS", "tape": a.tape,
        "collector": "src/pmus_book_tape.py, 2 s polling, full depth",
        "windows_with_enough_depth": len(windows),
        "windows_total": len(book),
        "per_window": {},
        "LABELS": ("No fill is claimed. Queue sizes are public ladder "
                   "levels. A cleared queue is necessary for a fill, never "
                   "sufficient. Order identity and FIFO position are not "
                   "observable from snapshots."),
    }
    agg: Dict[str, Dict[int, Dict[str, List[float]]]] = {
        s: {k: {"queue": [], "reach": [], "clear": []} for k in OFFSETS}
        for s in ("UP", "DOWN")}
    for slug in sorted(windows):
        lo = level_opportunity(bbo.get(slug) or [], book[slug])
        rep["per_window"][slug] = {
            "book_snapshots": len(book[slug]),
            "bbo_rows": len(bbo.get(slug) or []),
            "errors": errs.get(slug, 0),
            "quote_dynamics": quote_dynamics(bbo.get(slug) or [],
                                             book[slug]),
            "five_hundred": five_hundred_check(bbo.get(slug) or [],
                                               book[slug]),
            "levels": lo,
        }
        for s in ("UP", "DOWN"):
            for k in OFFSETS:
                t = lo[s]["ticks_behind_touch"][str(k)]
                for name, key in (("queue", "queue_at_placement_p50"),
                                  ("reach",
                                   "volume_through_in_horizon_p50"),
                                  ("clear",
                                   "share_queue_cleared_in_horizon")):
                    if t.get(key) is not None:
                        agg[s][k][name].append(t[key])
    rep["pooled_by_ticks_behind"] = {
        s: {str(k): {"windows": len(v["queue"]),
                     "queue_p50_of_window_medians": _pct(v["queue"], 0.5),
                     "opposing_volume_p50": _pct(v["reach"], 0.5),
                     "share_queue_cleared_p50": _pct(v["clear"], 0.5)}
            for k, v in agg[s].items()} for s in agg}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: rep[k] for k in
                      ("VENUE", "windows_with_enough_depth", "windows_total",
                       "pooled_by_ticks_behind")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
