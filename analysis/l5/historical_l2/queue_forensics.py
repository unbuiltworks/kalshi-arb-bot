"""PART A -- how did L5 obtain maker fills despite the queue?

THE QUESTION BEHIND THE QUESTION. Before measuring how deep L5's queue was, ask
whether the visible book is the queue he was standing in at all. On this venue
two BUYERS of opposite outcomes are matched by MINTING a complete set: each pays
their price, $1 is minted, one token goes to each. That match consumes NO offer
on either buyer's own book. For such a fill, depth resting at his price on his
own book is not "the queue ahead of him" -- it is a bystander.

So every fill is measured against BOTH candidate queue variables:

    OWN_BOOK_QUEUE    size resting at his price on his own bid book, plus the
                      size at strictly better bids. The conventional answer.
    COMPLEMENT_POOL   size bid at 1-p on the COMPLEMENT token -- the buyers he
                      could have been minted against. The mint answer.

and classified by the discriminator already established on this dataset: a
maker BUY priced at or above the prevailing ask cannot be a bid resting in that
book, and 99.86% of those are mint-feasible against 1.5% of the resting-bid
ones. That is close to a perfect separator, so it is used as one -- and the two
queue variables are then reported PER CLASS. If the conventional queue is the
right variable, it should bind on both classes. If it only binds on one, it is
the wrong variable for the other.

WHAT IS NOT CLAIMED. No order here is attributed to any trader. Depth is size
at a price in a public book; a level existing before his fill is a level
existing, not his order. Pro-rata allocation cannot be tested at all on a FIFO
CLOB with anonymous depth, and is reported as UNIDENTIFIABLE rather than
excluded by assertion.

TIMING IS READ STRICTLY BEFORE THE FILL. A snapshot taken AT the fill already
contains its effect and would collapse every depth toward zero.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.historical_l2.bbo_offset import (MAX_CARRY_S, OFFSET_AT_S,
                                                  placement_consistency)

PAR = D("1.00")
TICK = D("0.01")
# How far back to look for the first moment the market offered his price.
OPPORTUNITY_LOOKBACK_S = 900.0
# Depletion window after the fill. Labelled DEPLETION, never "traded volume":
# a level can shrink because it traded or because it was cancelled, and an
# anonymous book cannot tell those apart.
DEPLETION_WINDOW_S = 30.0


class L2:
    """Full-depth snapshots for one token, searchable by time."""

    def __init__(self) -> None:
        self.t: List[float] = []
        self.rows: List[dict] = []

    def add(self, ts: float, row: dict) -> None:
        self.t.append(ts)
        self.rows.append(row)

    def sort(self) -> None:
        o = sorted(range(len(self.t)), key=lambda i: self.t[i])
        self.t = [self.t[i] for i in o]
        self.rows = [self.rows[i] for i in o]

    def at(self, ts: float) -> Optional[dict]:
        """The book as of `ts`. A row is carried at most MAX_CARRY_S, the
        collector's own resync interval; beyond that it is refused."""
        i = bisect.bisect_right(self.t, ts) - 1
        if i < 0 or ts - self.t[i] > MAX_CARRY_S:
            return None
        return self.rows[i]

    def span(self, t0: float, t1: float):
        return zip(self.t[bisect.bisect_left(self.t, t0):bisect.bisect_right(self.t, t1)],
                   self.rows[bisect.bisect_left(self.t, t0):bisect.bisect_right(self.t, t1)])


def size_at(row: Optional[dict], price: Any, side: str = "bid") -> Optional[float]:
    if not row:
        return None
    ps, ss = row.get(side + "_prices") or [], row.get(side + "_sizes") or []
    p = D(str(price))
    tot = 0.0
    for a, b in zip(ps, ss):
        if D(str(a)) == p:
            tot += float(b)
    return tot


def size_better(row: Optional[dict], price: Any, side: str = "bid") -> Optional[float]:
    """Size at strictly better prices -- ahead of us on price priority."""
    if not row:
        return None
    ps, ss = row.get(side + "_prices") or [], row.get(side + "_sizes") or []
    p = D(str(price))
    tot = 0.0
    for a, b in zip(ps, ss):
        q = D(str(a))
        if (q > p) if side == "bid" else (q < p):
            tot += float(b)
    return tot


def reach(book: L2, t0: float, t1: float, price: Any) -> Dict[str, Any]:
    """Did the market come to a resting BUY at `price` before the fill?

    A buyer needs a SELLER at or below their price, so the ask is the side that
    has to move. `through` means the ask went strictly below the price, i.e. the
    market traded past it rather than merely touching it.
    """
    p = float(price)
    first = None
    reached = through = False
    for ts, r in book.span(t0, t1):
        a = r.get("best_ask")
        if a is None:
            continue
        if float(a) <= p + 1e-9:
            reached = True
            if first is None:
                first = ts
        if float(a) < p - 1e-9:
            through = True
    return {"reached": reached, "through": through, "t_first_opportunity": first,
            "seconds_from_opportunity_to_fill": (None if first is None else t1 - first)}


def mechanism(cls: str, mint_ok: Optional[bool], reached: bool) -> str:
    """One label per fill, from evidence only.

    PRO_RATA is not a possible answer here and is never assigned: this is a FIFO
    CLOB and the depth is anonymous, so no measurement in this dataset could
    distinguish pro-rata allocation. It is folded into UNIDENTIFIABLE.
    """
    if cls == "NO_BOOK":
        return "NO_BOOK"
    if cls == "AT_OR_ABOVE_ASK_NOT_MEASURABLE":
        return "MINT_MATCH_FEASIBLE" if mint_ok else "ABOVE_ASK_NOT_MINT_FEASIBLE"
    if cls == "INSIDE_SPREAD_IMPROVED_THE_TOUCH":
        return "NEWLY_POSTED_IMPROVED_THE_TOUCH"
    if reached:
        return "BOOK_FILL_FIFO_CONSISTENT"
    return "RESTING_BID_PRICE_NEVER_OFFERED"


def complements(d: dict, coin: str = "btc") -> Dict[str, str]:
    by: Dict[str, set] = defaultdict(set)
    for i, c in enumerate(d["coin"]):
        if c == coin:
            by[d["market_slug"][i]].add(str(d["token_id"][i]))
    out: Dict[str, str] = {}
    for toks in by.values():
        if len(toks) == 2:
            a, b = sorted(toks)
            out[a], out[b] = b, a
    return out


def forensics(fills: Sequence[dict], books: Dict[str, L2], comp: Dict[str, str],
              lead: int = 1) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for f in fills:
        tok, t, px = str(f["token_id"]), float(f["t"]), f["price"]
        bk = books.get(tok)
        if bk is None:
            continue
        own = bk.at(t - lead)
        cls = placement_consistency(own, px)
        ct = comp.get(tok)
        cb = books[ct].at(t - lead) if ct in books else None
        cbid = None if cb is None else cb.get("best_bid")
        pair = None if cbid is None else float(D(str(px)) + D(str(cbid)))
        mint = None if pair is None else pair >= float(PAR)
        rc = reach(bk, t - OPPORTUNITY_LOOKBACK_S, t, px)
        after = bk.at(t + DEPLETION_WINDOW_S)
        d0 = size_at(own, px)
        d1 = size_at(after, px)
        rows.append({
            "tx": f.get("tx"), "token_id": tok, "t": t, "price": float(px),
            "size": float(f.get("size") or 0.0), "market_slug": f.get("market_slug"),
            "class": cls,
            "own_depth_at_price": d0,
            "own_depth_better_priced": size_better(own, px),
            "own_best_bid": None if own is None else own.get("best_bid"),
            "own_best_ask": None if own is None else own.get("best_ask"),
            "complement_best_bid": cbid,
            "complement_pool_at_1_minus_p":
                (None if cb is None else size_at(cb, round(1.0 - float(px), 4))),
            "pair_sum": pair, "mint_feasible": mint,
            "reached": rc["reached"], "through": rc["through"],
            "seconds_from_opportunity_to_fill": rc["seconds_from_opportunity_to_fill"],
            "own_depth_depletion_30s": (None if (d0 is None or d1 is None) else d0 - d1),
            "mechanism": mechanism(cls, mint, rc["reached"]),
        })
    return rows


def _pct(v: Sequence[float], q: float) -> Optional[float]:
    s = sorted(x for x in v if x is not None)
    return s[int(q * (len(s) - 1))] if s else None


def summarize(rows: Sequence[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": len(rows), "by_mechanism": {}}
    for m in sorted({r["mechanism"] for r in rows}):
        s = [r for r in rows if r["mechanism"] == m]
        out["by_mechanism"][m] = {
            "n": len(s), "share": round(len(s) / len(rows), 4) if rows else None,
            "reached": sum(1 for r in s if r["reached"]),
            "through": sum(1 for r in s if r["through"]),
            "own_depth_at_price_p50": _pct([r["own_depth_at_price"] for r in s], 0.5),
            "own_depth_at_price_p90": _pct([r["own_depth_at_price"] for r in s], 0.9),
            "own_depth_better_priced_p50": _pct([r["own_depth_better_priced"] for r in s], 0.5),
            "complement_pool_p50": _pct([r["complement_pool_at_1_minus_p"] for r in s], 0.5),
            "complement_pool_p90": _pct([r["complement_pool_at_1_minus_p"] for r in s], 0.9),
            "secs_opportunity_to_fill_p50":
                _pct([r["seconds_from_opportunity_to_fill"] for r in s], 0.5),
            "fill_size_p50": _pct([r["size"] for r in s], 0.5),
        }
    out["labels"] = (
        "own_depth_* and complement_pool_* are sizes at prices in a public book. "
        "No order is attributed to any trader. own_depth_depletion_30s is "
        "DEPLETION -- trades and cancels are indistinguishable in anonymous "
        "depth and it is never reported as traded volume. PRO_RATA is not "
        "assignable on a FIFO CLOB with anonymous depth and never appears.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/historical_l2/block_times.json")
    ap.add_argument("--coin", default="btc")
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/queue_forensics.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, L2] = defaultdict(L2)
    for i, c in enumerate(d["coin"]):
        if c != a.coin:
            continue
        books[str(d["token_id"][i])].add(d["captured_at"][i].timestamp(), {
            "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
            "bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i],
            "ask_prices": d["ask_prices"][i], "ask_sizes": d["ask_sizes"][i]})
    for b in books.values():
        b.sort()
    comp = complements(d, a.coin)
    bt = json.load(open(a.block_times))["block_times"]

    fills = []
    for w in json.load(open(a.backfill))["rows"]:
        for lg in w["fills"]:
            if lg.get("l5_role") != "maker":
                continue
            t = bt.get(str(lg.get("block")))
            if t is None:
                continue
            fills.append({"tx": lg["tx"], "token_id": lg["token_id"],
                          "price": lg["price"], "size": lg["size"],
                          "t": float(t), "market_slug": w["market_slug"]})

    rep: Dict[str, Any] = {"n_maker_fills_with_time": len(fills), "by_lead": {}}
    for lead in OFFSET_AT_S:
        rows = forensics(fills, books, comp, lead=lead)
        rep["by_lead"]["T-%d" % lead] = summarize(rows)
        if lead == a.lead:
            rep["rows_sample"] = rows[:200]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep["by_lead"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
