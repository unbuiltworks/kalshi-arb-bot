"""How far behind the best bid did L5's maker fills actually rest?

THE QUESTION THE PROSPECTIVE GRID DEPENDS ON. Arm D tests 0 to -4 ticks. If
L5's maker fills sit at the touch through -2, testing -4 wastes the experiment;
if the mass sits at -5 to -8, the grid is too shallow to find anything. This
answers it from 1-second historical L2 joined to his fills by TOKEN ID.

THE OFFSET IS MEASURED BEFORE THE FILL, NOT AT IT. A book snapshot taken AT the
fill already contains the fill's effect, so the offset would collapse toward
zero by construction. Offsets are therefore read at T-30, T-5 and T-1 and
reported separately -- if they disagree, the disagreement is the finding.

SPLIT THREE WAYS, because the legs are not interchangeable:

    FIRST_FILL              how he opens
    FIRST_OPPOSITE_FILL     the leg our clone has never reproduced
    ALL_BTC15M_MAKER_FILLS  the whole population

ANONYMOUS LIQUIDITY IS NEVER IDENTIFIED AS L5'S. The dataset shows depth, not
ownership. "Size at the eventual fill price" is size at a price, and a level
existing before his fill is a level existing -- not his order.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

TICK = D("0.01")
# The capture writes a row only when the book CHANGES, so an old row is the
# current state, not stale data. It is bounded by the collector's own 60s REST
# resync: beyond that, undetectable delta drift is outside the dataset's stated
# accuracy and the row is refused rather than carried.
MAX_CARRY_S = 60.0
# The feed carries ~24k events/s with no sequence numbers, so dropped messages
# cannot be detected from the stream. Offsets therefore carry up to one resync
# interval of drift, and that is reported rather than assumed away.
DRIFT_BOUND_S = 60.0
OFFSET_AT_S = (30, 5, 1)
SNAPSHOT_GRID_S = (120, 60, 30, 15, 10, 5, 2, 1, 0)
POPULATIONS = ("FIRST_FILL", "FIRST_OPPOSITE_FILL", "ALL_BTC15M_MAKER_FILLS")


class Book:
    """1-second snapshots for one token, searchable by time."""

    def __init__(self) -> None:
        self.t: List[float] = []
        self.rows: List[dict] = []

    def add(self, ts: float, row: dict) -> None:
        self.t.append(ts)
        self.rows.append(row)

    def sort(self) -> None:
        order = sorted(range(len(self.t)), key=lambda i: self.t[i])
        self.t = [self.t[i] for i in order]
        self.rows = [self.rows[i] for i in order]

    def at(self, ts: float, *, max_age_s: float = MAX_CARRY_S) -> Optional[dict]:
        """The latest snapshot at or before `ts`. An OLD ROW IS NOT STALE.

        The capture writes a row only when the book CHANGES, so a gap between
        consecutive rows means the book did not move -- the last row is the
        correct state throughout. An earlier version of this discarded rows
        older than three seconds as stale, which would have thrown away every
        quiet moment and biased the whole offset distribution toward volatile
        ones.

        It is still bounded. The collector re-reads the full book from REST
        every 60 s to cap undetectable delta drift, so a row carried further
        than that is beyond the dataset's own accuracy guarantee and is
        refused rather than trusted.
        """
        if not self.t:
            return None
        i = bisect.bisect_right(self.t, ts) - 1
        if i < 0:
            return None
        age = ts - self.t[i]
        if age > max_age_s:
            return None
        return {**self.rows[i], "carried_s": round(age, 1)}

    def level_present_s(self, price: D, *, until: float,
                        lookback_s: float = 300.0) -> Optional[float]:
        """How long some size existed at `price` continuously before `until`.

        SIZE AT A PRICE, NEVER OWNERSHIP. This says a level existed, not that
        it was L5's order.
        """
        if not self.t:
            return None
        i = bisect.bisect_right(self.t, until) - 1
        if i < 0:
            return None
        last = None
        while i >= 0 and until - self.t[i] <= lookback_s:
            if _size_at(self.rows[i], price) <= 0:
                break
            last = self.t[i]
            i -= 1
        return None if last is None else round(until - last, 1)


def _size_at(row: dict, price: D) -> D:
    for p, s in zip(row["bid_prices"], row["bid_sizes"]):
        if D(str(p)) == price:
            return D(str(s))
    return D("0")


def offset_ticks(best_bid: Any, fill_px: Any) -> Optional[int]:
    """Ticks BELOW the touch. 0 = joined the touch; positive = deeper.

    A NEGATIVE RESULT IS NOT FOLDED IN. A fill above the prevailing best bid
    means the book moved between the snapshot and the fill, or the level was
    created by the fill itself; calling that "-1 tick" would invent depth that
    was never rested at.
    """
    try:
        bb, px = D(str(best_bid)), D(str(fill_px))
    except Exception:                                         # noqa: BLE001
        return None
    if bb <= 0:
        return None
    return int(((bb - px) / TICK).to_integral_value())


def placement_consistency(snap: Optional[dict], price: Any) -> str:
    """Could this maker BUY have been a bid resting in THIS book?

    Every L5 maker fill in the overlap is a buy -- the log's makerAssetId is
    the collateral and takerAssetId is the token, checked on samples from both
    tails. A resting bid therefore cannot sit above the prevailing ask, and a
    row that does is not a deep quote: it is a snapshot from the wrong moment,
    or a quote placed after it. Such rows are COUNTED AND REPORTED rather than
    bucketed as "ABOVE_BBO" and folded into the distribution, because the grid
    decision must not be read off rows whose depth is not measurable.
    """
    if not snap or snap.get("best_bid") is None:
        return "NO_BOOK"
    try:
        px, bb = D(str(price)), D(str(snap["best_bid"]))
        ba = None if snap.get("best_ask") is None else D(str(snap["best_ask"]))
    except Exception:                                         # noqa: BLE001
        return "NO_BOOK"
    if px <= bb:
        return "RESTING_BID_CONSISTENT"
    if ba is not None and px >= ba:
        return "AT_OR_ABOVE_ASK_NOT_MEASURABLE"
    return "INSIDE_SPREAD_IMPROVED_THE_TOUCH"


def bucket(t: Optional[int]) -> str:
    if t is None:
        return "UNKNOWN"
    if t < 0:
        return "ABOVE_BBO"
    return f"-{t}" if t else "join"


def build(fills: Sequence[dict], books: Dict[str, Book]) -> List[Dict[str, Any]]:
    """One row per L5 maker fill, with its offset at each lead time."""
    out = []
    for f in fills:
        bk = books.get(str(f["token_id"]))
        if bk is None:
            out.append({**_id(f), "status": "NO_BOOK_FOR_TOKEN"})
            continue
        if f.get("t") is None:
            out.append({**_id(f), "status": "NO_BLOCK_TIMESTAMP"})
            continue
        t = float(f["t"])
        row: Dict[str, Any] = {**_id(f), "status": "OK", "snapshots": {}}
        for lead in SNAPSHOT_GRID_S:
            s = bk.at(t - lead)
            row["snapshots"][f"T-{lead}"] = (
                None if s is None else {
                    "best_bid": s["best_bid"], "best_ask": s["best_ask"],
                    "spread": (None if s["best_ask"] is None
                               or s["best_bid"] is None
                               else round(s["best_ask"] - s["best_bid"], 4)),
                    "size_at_fill_price": str(_size_at(s, D(str(f["price"])))),
                    "offset_ticks": offset_ticks(s["best_bid"], f["price"]),
                    "seconds_to_close": s.get("seconds_to_close"),
                    "carried_s": s.get("carried_s"),
                })
        for lead in OFFSET_AT_S:
            s = row["snapshots"].get(f"T-{lead}")
            row[f"offset_T-{lead}"] = (s or {}).get("offset_ticks")
            row[f"consistency_T-{lead}"] = placement_consistency(s, f["price"])
        row["level_present_s_before_fill"] = bk.level_present_s(
            D(str(f["price"])), until=t)
        out.append(row)
    return out


def _id(f: dict) -> Dict[str, Any]:
    return {"tx": f.get("tx"), "token_id": str(f.get("token_id")),
            "market_slug": f.get("market_slug"), "t": f.get("t"),
            "price": f.get("price"), "size": f.get("size"),
            "role": f.get("l5_role"), "population": f.get("population")}


def table(rows: Sequence[dict], *, lead: int) -> Dict[str, Any]:
    """The decision table: BBO offset -> L5 maker fills -> share."""
    out: Dict[str, Any] = {}
    for pop in POPULATIONS:
        sub = [r for r in rows
               if r.get("role") == "maker"
               and (pop == "ALL_BTC15M_MAKER_FILLS" or r.get("population") == pop)]
        cons = Counter(r.get(f"consistency_T-{lead}") for r in sub)
        # THE GRID IS READ OFF MEASURABLE ROWS ONLY. A fill whose price was at
        # or above the prevailing ask has no measurable resting depth, and
        # counting it as "0 ticks" or "ABOVE_BBO" would bias the answer toward
        # the touch -- exactly the direction that decides the grid.
        meas = [r for r in sub
                if r.get(f"consistency_T-{lead}") == "RESTING_BID_CONSISTENT"]
        c = Counter(bucket(r.get(f"offset_T-{lead}")) for r in meas)
        known = sum(v for k, v in c.items() if k not in ("UNKNOWN",))
        out[pop] = {
            "n_maker_fills": len(sub),
            "placement_consistency": dict(cons),
            "n_measurable_resting_bids": len(meas),
            "with_a_book": known,
            "by_offset": {k: {"fills": v,
                              "share": (round(v / known, 4) if known else None)}
                          for k, v in _ordered(c)},
            "cumulative_join_through_minus2": (
                round(sum(c.get(k, 0) for k in ("join", "-1", "-2")) / known, 4)
                if known else None),
            "mass_beyond_minus4": (
                round(sum(v for k, v in c.items()
                          if k.startswith("-") and k not in
                          ("-1", "-2", "-3", "-4")) / known, 4)
                if known else None),
        }
    return out


def _ordered(c: Counter) -> List[Tuple[str, int]]:
    def key(k):
        if k == "join":
            return (0, 0)
        if k == "ABOVE_BBO":
            return (-1, 0)
        if k == "UNKNOWN":
            return (99, 0)
        return (1, int(k.lstrip("-")))
    return sorted(c.items(), key=lambda kv: key(kv[0]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/historical_l2/block_times.json")
    ap.add_argument("--out", default="analysis/l5/historical_l2/bbo_offset.json")
    a = ap.parse_args(argv)

    block_times: Dict[str, int] = {}
    if a.block_times and os.path.exists(a.block_times):
        block_times = json.load(open(a.block_times))["block_times"]
    print(f"{len(block_times)} real block timestamps", flush=True)

    import pyarrow.parquet as pq
    t = pq.read_table(a.parquet)
    d = t.to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        books[str(d["token_id"][i])].add(
            d["captured_at"][i].timestamp(),
            {"best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
             "bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i],
             "seconds_to_close": d["seconds_to_close"][i]})
    for b in books.values():
        b.sort()
    print(f"books for {len(books)} tokens", flush=True)

    bf = json.load(open(a.backfill))
    fills = []
    for w in bf["rows"]:
        byt: Dict[str, List[dict]] = defaultdict(list)
        for lg in w["fills"]:
            byt[str(lg["token_id"])].append(lg)
        ordered = sorted(w["fills"], key=lambda f: (f.get("block") or 0,
                                                    f.get("log_index") or 0))
        first_side = ordered[0]["token_id"] if ordered else None
        seen_opp = False
        for n, lg in enumerate(ordered):
            pop = None
            if n == 0:
                pop = "FIRST_FILL"
            elif not seen_opp and lg["token_id"] != first_side:
                pop, seen_opp = "FIRST_OPPOSITE_FILL", True
            fills.append({**lg, "market_slug": w["market_slug"],
                          "population": pop,
                          "t": _t_of(lg, w, block_times)})
    print(f"{len(fills)} L5 fills to place", flush=True)
    rows = build(fills, books)
    rep = {"fills": len(rows),
           "placed": sum(1 for r in rows if r["status"] == "OK"),
           "tables": {f"T-{l}": table(rows, lead=l) for l in OFFSET_AT_S},
           "rows": rows[:120],
           "labels": ("offsets read BEFORE the fill, never at it. Anonymous "
                      "depth is never identified as L5's order. An old "
                      "snapshot is the CURRENT state -- the capture writes "
                      "only on change -- carried at most "
                      f"{MAX_CARRY_S:.0f}s, the collector's own REST resync "
                      "interval. Undetectable delta drift within that "
                      "interval is the dataset's stated accuracy bound and "
                      "applies to every offset here."),
           "accuracy_bound": {
               "max_carry_s": MAX_CARRY_S, "drift_bound_s": DRIFT_BOUND_S,
               "why": ("~24k events/s with no sequence numbers: a dropped "
                       "delta cannot be detected from the stream, and the "
                       "60s REST resync is what bounds the resulting drift")}}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep["tables"], indent=1, default=str))
    return 0


def _t_of(lg: dict, w: dict,
          block_times: Optional[Dict[str, int]] = None) -> Optional[float]:
    """The fill's REAL block timestamp. None when it is not known.

    NOT INTERPOLATED ANY MORE. Linear interpolation across the window put a
    quarter of L5's maker BUYS at or above the prevailing ask -- impossible for
    a resting bid -- and the rate fell as the lead shortened, which is what a
    mistimed snapshot looks like. Returning None rather than an estimate keeps
    an unknown time an UNKNOWN offset instead of a confident wrong one.
    """
    b = lg.get("block")
    if b is None or not block_times:
        return None
    t = block_times.get(str(b))
    return float(t) if t is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
