"""P10-B: decode a trade's price space against the book, or refuse to.

    python3 analysis/gemini/p10b_decode.py <capture.jsonl> [max_stale_s]

TWO HYPOTHESES, TESTED, NEITHER ASSUMED:

    H1  the printed price p is in the CONTRACT's (YES) space
    H2  the printed price is the COMPLEMENT's, so the contract-space price
        is 1 - p

A trade is classified only when exactly one hypothesis is compatible with a
book observed close enough in time. Compatible means the price lies inside
[bestBid - tol, bestAsk + tol]: the venue's own definition of `type` is
"buy means that an ask was removed from the book by an incoming buy order,
sell means that a bid was removed from the book by an incoming sell order",
so an execution price must be a price that was ON the book.

Everything else is AMBIGUOUS and stays AMBIGUOUS. In particular:

  * both readings inside the book -> AMBIGUOUS (a wide or near-0.50 book
    cannot tell p from 1-p)
  * neither reading inside       -> AMBIGUOUS (the book moved, or our snapshot
    is wrong; it is not evidence for either side)
  * no book within `max_stale_s` -> STALE, reported separately, never folded
    into a classified bucket
  * printed before this capture first saw the symbol -> PRE_CAPTURE, which is
    backfill rather than a failed observation

STALENESS IS MEASURED, NOT ASSUMED. Each trade carries the gap to the book
before it and the book after it, and the poll's own round trip. A
"contemporaneous" book that is four seconds old in a market that moves every
second is not contemporaneous, and the coverage table says so.

NO STRATEGY, NO THRESHOLD TUNING, NO FILLS. This module only labels rows.
"""
import json
import sys
from collections import defaultdict
from fractions import Fraction as F

TOL = F(1, 100)          # one tick of slack on each side of the book


def load(path):
    books, trades = defaultdict(list), []
    for ln in open(path):
        try:
            d = json.loads(ln)
        except Exception:                                      # noqa: BLE001
            continue
        k = d.get("kind")
        if k == "book":
            if d.get("bid") in (None, "") or d.get("ask") in (None, ""):
                continue
            books[d["symbol"]].append(d)
        elif k == "trade":
            trades.append(d)
    for v in books.values():
        v.sort(key=lambda r: r["t_recv"])
    return books, trades


def bracket(book_rows, t):
    """The last book observed at or before t, and the first at or after."""
    prev = nxt = None
    for r in book_rows:
        if r["t_recv"] <= t:
            prev = r
        else:
            nxt = r
            break
    return prev, nxt


def inside(p, row):
    if row is None:
        return None
    bid, ask = F(str(row["bid"])), F(str(row["ask"]))
    lo, hi = min(bid, ask) - TOL, max(bid, ask) + TOL
    return lo <= p <= hi


def classify_one(tr, books, max_stale_s):
    t = (tr.get("tms") or 0) / 1000.0
    rows = books.get(tr["symbol"]) or []
    prev, nxt = bracket(rows, t)
    age_prev = (t - prev["t_recv"]) if prev else None
    age_next = (nxt["t_recv"] - t) if nxt else None
    near = [r for r, a in ((prev, age_prev), (nxt, age_next))
            if r is not None and a is not None and a <= max_stale_s]
    out = {"tid": tr.get("tid"), "symbol": tr["symbol"],
           "category": tr.get("category"), "subcategory": tr.get("subcategory"),
           "price": tr.get("price"), "amount": tr.get("amount"),
           "type": tr.get("type"), "tms": tr.get("tms"),
           "age_prev_s": age_prev, "age_next_s": age_next,
           "rtt_trade": tr.get("rtt"),
           "book_prev": (None if prev is None else [prev["bid"], prev["ask"]]),
           "book_next": (None if nxt is None else [nxt["bid"], nxt["ask"]])}
    if prev is None and rows:
        #: the trade happened BEFORE this capture saw the symbol at all. It is
        #: backfill from the first poll, not a stale observation, and mixing
        #: the two would make early coverage look worse than it is.
        out["label"] = "PRE_CAPTURE"
        return out
    if not near:
        out["label"] = "STALE"
        return out
    p = F(str(tr["price"]))
    h1 = any(inside(p, r) for r in near)
    h2 = any(inside(F(1) - p, r) for r in near)
    out["h1_fits"], out["h2_fits"] = h1, h2
    if h1 and not h2:
        out["label"] = "H1"
    elif h2 and not h1:
        out["label"] = "H2"
    elif h1 and h2:
        out["label"] = "AMBIGUOUS_BOTH"
    else:
        out["label"] = "AMBIGUOUS_NEITHER"
    # internal consistency: do the book before and the book after agree?
    if prev is not None and nxt is not None:
        a = (inside(p, prev), inside(F(1) - p, prev))
        b = (inside(p, nxt), inside(F(1) - p, nxt))
        out["prev_next_agree"] = (a == b)
    return out


def main(path, max_stale_s=5.0):
    max_stale_s = float(max_stale_s)
    books, trades = load(path)
    rows = [classify_one(t, books, max_stale_s) for t in trades]
    by = defaultdict(lambda: defaultdict(int))
    disagree = defaultdict(lambda: [0, 0])
    for r in rows:
        k = (r.get("category") or "?", r.get("subcategory") or "?")
        by[k][r["label"]] += 1
        if r.get("prev_next_agree") is not None:
            disagree[k][0] += 1
            disagree[k][1] += int(not r["prev_next_agree"])
    print(f"capture: {path}")
    print(f"trades {len(trades)}, symbols with books {len(books)}, "
          f"max staleness allowed {max_stale_s:.1f}s, tolerance "
          f"{float(TOL):.2f}")
    ages = sorted(r["age_prev_s"] for r in rows if r.get("age_prev_s") is not None)
    rtts = sorted(r["rtt_trade"] for r in rows if r.get("rtt_trade"))
    if ages:
        print(f"  book staleness before a trade: median {ages[len(ages)//2]:.2f}s "
              f"p90 {ages[int(len(ages)*0.9)]:.2f}s")
    if rtts:
        print(f"  trades-poll round trip: median {rtts[len(rtts)//2]:.3f}s")
    print(f"\n{'category':>12} {'subcategory':>20} {'n':>5} {'H1':>6} {'H2':>6} "
          f"{'AMB both':>9} {'AMB neither':>12} {'STALE':>7} {'pre':>5} "
          f"{'decoded/obs':>12} "
          f"{'prev/next disagree':>19}")
    for k, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        dec = c["H1"] + c["H2"]
        obs = n - c["PRE_CAPTURE"]
        d = disagree[k]
        print(f"{k[0]:>12} {str(k[1])[:20]:>20} {n:>5} {c['H1']:>6} {c['H2']:>6} "
              f"{c['AMBIGUOUS_BOTH']:>9} {c['AMBIGUOUS_NEITHER']:>12} "
              f"{c['STALE']:>7} {c['PRE_CAPTURE']:>5} "
              f"{(dec / obs if obs else 0):>11.1%} "
              f"{(d[1] / d[0] if d[0] else 0):>18.1%}")
    return rows


if __name__ == "__main__":
    main(*sys.argv[1:3])
