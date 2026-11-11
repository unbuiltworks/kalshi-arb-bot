"""C: can a DUMB continuously-resting dual-sided maker reproduce L5's trajectory?

THE NULL THAT MUST BE BEATEN BEFORE ANY CONTROLLER IS BUILT. H3 says L5 quotes
both outcomes with stable rules and the observed side sequence is manufactured
by whoever happens to hit him. If a simulator with NO next-side decision, NO
pair gate and NO inventory logic reproduces his fill density, side sequence and
pair-cost path, then the "controller" we were about to build is explaining
noise.

THE RULES, deliberately simple:
  * rest a BID on BOTH tokens at (best_bid - k ticks), continuously
  * re-quote whenever the touch moves; never cross, never sell
  * no next-side choice -- historical flow decides which side fills
  * no <=0.96 pair gate at any point
  * fixed size per fill

FILL RULE, and its honest limitation. The dataset is BOOK ONLY -- it carries no
trade prints -- so VOLUME_AHEAD_CLEARED cannot be computed here. The strictest
criterion this data supports is that the observed best bid falls STRICTLY BELOW
our price: every order at our level is gone from the book, so an order resting
at the back of that level is gone too. That is conservative about queue
position and is the closest available analogue of TRADE_THROUGH.

It cannot separate a level that TRADED from one that was CANCELLED, and that
ambiguity is reported rather than assumed away. A cancel-driven "fill" makes
this null LOOK BETTER than it is, so the comparison is biased in the null's
favour -- which is the safe direction for a test whose purpose is to refuse a
controller.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

TICK = D("0.01")
OFFSETS = (0, 1, 2)
SIZE = D("10")


def simulate_token(book, offset_ticks: int, size: D) -> List[Dict[str, Any]]:
    """Fills for one continuously-resting bid on one token."""
    fills = []
    quote: Optional[D] = None
    for t, row in zip(book.t, book.rows):
        bb = row.get("best_bid")
        if bb is None:
            continue
        bb = D(str(bb))
        if quote is None:
            quote = bb - TICK * offset_ticks
            continue
        # STRICT: the whole level cleared out of the book
        if bb < quote:
            fills.append({"t": t, "price": str(quote), "size": str(size)})
            quote = bb - TICK * offset_ticks      # immediately re-quote
        elif bb - TICK * offset_ticks != quote:
            quote = bb - TICK * offset_ticks      # follow the touch
    return fills


def trajectory(up_fills, dn_fills) -> Dict[str, Any]:
    """Merge both sides chronologically and walk the same inventory state."""
    ev = ([{**f, "side": "Up"} for f in up_fills]
          + [{**f, "side": "Down"} for f in dn_fills])
    ev.sort(key=lambda e: e["t"])
    up_q = dn_q = D("0")
    up_c = dn_c = D("0")
    path = []
    same = tot = 0
    prev = None
    for e in ev:
        q, px = D(e["size"]), D(e["price"])
        if e["side"] == "Up":
            up_q += q
            up_c += q * px
        else:
            dn_q += q
            dn_c += q * px
        if prev is not None:
            tot += 1
            same += 1 if prev == e["side"] else 0
        prev = e["side"]
        uv = (up_c / up_q) if up_q else None
        dv = (dn_c / dn_q) if dn_q else None
        path.append({
            "side": e["side"],
            "matched_qty": float(min(up_q, dn_q)),
            "pair_cost": (float(uv + dv) if (uv is not None and dv is not None)
                          else None),
            "residual_qty": float(abs(up_q - dn_q)),
            "residual_rel": (float(abs(up_q - dn_q) / (up_q + dn_q))
                             if (up_q + dn_q) else None),
        })
    return {"n_fills": len(ev), "path": path,
            "same_side_persistence": (round(same / tot, 4) if tot else None),
            "final": (path[-1] if path else None)}


def _summ(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    s = sorted(xs)
    return {"n": len(s), "p10": round(s[int(0.1 * (len(s) - 1))], 4),
            "median": round(s[len(s) // 2], 4),
            "p90": round(s[int(0.9 * (len(s) - 1))], 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--out", default="analysis/l5/passive_null/null.json")
    a = ap.parse_args(argv)

    from collections import defaultdict
    import pyarrow.parquet as pq
    from analysis.l5.historical_l2.bbo_offset import Book

    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    tok_win: Dict[str, str] = {}
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        tid = str(d["token_id"][i])
        tok_win[tid] = d["market_slug"][i]
        books[tid].add(d["captured_at"][i].timestamp(),
                       {"best_bid": d["best_bid"][i],
                        "best_ask": d["best_ask"][i]})
    for b in books.values():
        b.sort()
    by_win: Dict[str, List[str]] = defaultdict(list)
    for tid, slug in tok_win.items():
        by_win[slug].append(tid)

    bf = json.load(open(a.backfill))
    l5 = {}
    for w in bf["rows"]:
        if not w["fills"]:
            continue
        toks = sorted({str(f["token_id"]) for f in w["fills"]})
        l5[w["market_slug"]] = {"n_fills": len(w["fills"]), "tokens": toks}

    rep: Dict[str, Any] = {"offsets": {}, "l5_windows": len(l5)}
    for off in OFFSETS:
        rows = []
        for slug, info in sorted(l5.items()):
            toks = by_win.get(slug) or []
            if len(toks) != 2:
                continue
            f0 = simulate_token(books[toks[0]], off, SIZE)
            f1 = simulate_token(books[toks[1]], off, SIZE)
            tr = trajectory(f0, f1)
            rows.append({"slug": slug, "l5_fills": info["n_fills"], **{
                k: v for k, v in tr.items() if k != "path"}})
        rep["offsets"][f"-{off}t"] = {
            "windows": len(rows),
            "null_fills_per_window": _summ([float(r["n_fills"]) for r in rows]),
            "l5_fills_per_window": _summ([float(r["l5_fills"]) for r in rows]),
            "null_same_side_persistence": _summ(
                [r["same_side_persistence"] for r in rows]),
            "null_final_pair_cost": _summ(
                [(r["final"] or {}).get("pair_cost") for r in rows]),
            "null_final_residual_rel": _summ(
                [(r["final"] or {}).get("residual_rel") for r in rows]),
            "null_final_matched_qty": _summ(
                [(r["final"] or {}).get("matched_qty") for r in rows]),
            "per_window": rows,
        }
    rep["l5_reference"] = {
        "same_side_persistence": 0.6824,
        "final_pair_cost_median": 0.9603,
        "final_residual_rel_median": 0.0979,
        "source": "527-window canonical set, transaction-aggregated",
    }
    rep["limitation"] = (
        "book-only data: a level vanishing cannot be separated from a cancel, "
        "so these fills are an UPPER bound on what a passive quote would have "
        "achieved. The bias favours the null, which is the safe direction for "
        "a test meant to refuse a controller.")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    for k, v in rep["offsets"].items():
        print(f"\n{k}: windows={v['windows']}")
        for f in ("null_fills_per_window", "l5_fills_per_window",
                  "null_same_side_persistence", "null_final_pair_cost",
                  "null_final_residual_rel"):
            print(f"   {f:<28} {v[f]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
