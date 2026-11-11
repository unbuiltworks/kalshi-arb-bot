"""P10-C: forensics on apparent (p, 1-p) pairs -- prove or falsify the mirror.

    python3 analysis/gemini/p10c_mirror.py <capture.jsonl> [<capture.jsonl> ...]

For every pair of trades on one instrument whose prices sum to 1 (within a
tick), this reports the things that distinguish "two legs of one match,
reported in two price spaces" from "two separate executions":

    tid            distinct ids do NOT rule out one match -- a venue may id
                   each leg -- but identical ids would settle it immediately
    ms separation  two legs of one match should share a millisecond, or nearly
    amount         two legs of one match must have the SAME quantity
    aggressor      the venue documents `type` as the side removed from the
                   book: "buy means that an ask was removed from the book by an
                   incoming buy order". One match has ONE aggressor, so two
                   rows with the same `type` are hard to read as one match
                   unless the two rows are in different books
    book fit       does each row sit inside the contract's own book, its
                   mirror, both or neither
    book moves     how many DISTINCT book states were observed between the two
                   rows. One match should move the book once; two executions
                   at mirrored prices should move it twice

This module states what it found. It does not promote a hypothesis to fact.
"""
import json
import sys
from collections import defaultdict
from fractions import Fraction as F

TOL = F(1, 100)


def load(paths):
    books, trades = defaultdict(list), defaultdict(list)
    for p in paths:
        for ln in open(p):
            try:
                d = json.loads(ln)
            except Exception:                                  # noqa: BLE001
                continue
            if d.get("kind") == "book" and d.get("bid") and d.get("ask"):
                books[d["symbol"]].append(d)
            elif d.get("kind") == "trade":
                trades[d["symbol"]].append(d)
    for v in books.values():
        v.sort(key=lambda r: r["t_recv"])
    for v in trades.values():
        v.sort(key=lambda r: (r.get("tms") or 0, r.get("tid") or 0))
    return books, trades


def fit(p, rows):
    """'own', 'mirror', 'both' or 'neither' against the nearest books."""
    if not rows:
        return None
    own = any(min(F(str(r["bid"])), F(str(r["ask"]))) - TOL <= p
              <= max(F(str(r["bid"])), F(str(r["ask"]))) + TOL for r in rows)
    mir = any(min(F(str(r["bid"])), F(str(r["ask"]))) - TOL <= (F(1) - p)
              <= max(F(str(r["bid"])), F(str(r["ask"]))) + TOL for r in rows)
    return ("both" if own and mir else
            "own" if own else "mirror" if mir else "neither")


def near_books(rows, t, window=5.0):
    return [r for r in rows if abs(r["t_recv"] - t) <= window]


def states_between(rows, t0, t1):
    seen = []
    for r in rows:
        if t0 <= r["t_recv"] <= t1:
            s = (r["bid"], r["ask"])
            if not seen or seen[-1] != s:
                seen.append(s)
    return len(set(seen)), len(seen)


def main(paths):
    books, trades = load(paths)
    pairs = []
    for sym, ts in trades.items():
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if (b.get("tms") or 0) - (a.get("tms") or 0) > 5000:
                    break
                if abs(F(str(a["price"])) + F(str(b["price"])) - 1) > TOL:
                    continue
                ta = (a.get("tms") or 0) / 1000.0
                tb = (b.get("tms") or 0) / 1000.0
                bk = books.get(sym) or []
                distinct, obs = states_between(bk, min(ta, tb) - 1, max(ta, tb) + 1)
                pairs.append({
                    "symbol": sym, "cat": a.get("category"),
                    "sub": a.get("subcategory"),
                    "tid_a": a.get("tid"), "tid_b": b.get("tid"),
                    "same_tid": a.get("tid") == b.get("tid"),
                    "ms_gap": (b.get("tms") or 0) - (a.get("tms") or 0),
                    "px_a": a["price"], "px_b": b["price"],
                    "amt_a": a["amount"], "amt_b": b["amount"],
                    "same_amount": F(str(a["amount"])) == F(str(b["amount"])),
                    "type_a": a.get("type"), "type_b": b.get("type"),
                    "same_type": a.get("type") == b.get("type"),
                    "fit_a": fit(F(str(a["price"])), near_books(bk, ta)),
                    "fit_b": fit(F(str(b["price"])), near_books(bk, tb)),
                    "book_states_between": distinct,
                })
    print(f"apparent (p, 1-p) pairs found: {len(pairs)}")
    if not pairs:
        print("  none yet -- the capture needs more trades on one instrument")
        return pairs
    n = len(pairs)
    same_tid = sum(p["same_tid"] for p in pairs)
    same_amt = sum(p["same_amount"] for p in pairs)
    same_typ = sum(p["same_type"] for p in pairs)
    ms = sorted(p["ms_gap"] for p in pairs)
    print(f"  identical tid            : {same_tid}/{n}")
    print(f"  identical amount         : {same_amt}/{n}")
    print(f"  identical aggressor type : {same_typ}/{n}")
    print(f"  ms separation            : median {ms[len(ms) // 2]}  "
          f"min {ms[0]}  max {ms[-1]}")
    fits = defaultdict(int)
    for p in pairs:
        fits[(p["fit_a"], p["fit_b"])] += 1
    print(f"  (fit_a, fit_b) against the contract's own book: {dict(fits)}")
    moves = defaultdict(int)
    for p in pairs:
        moves[p["book_states_between"]] += 1
    print(f"  distinct book states observed around the pair: {dict(moves)}")
    print("\n  rows:")
    for p in pairs[:25]:
        print(f"    {p['symbol'][:34]:>34} {p['px_a']:>6}/{p['px_b']:<6} "
              f"amt {p['amt_a']:>7}/{p['amt_b']:<7} {p['type_a']:>4}/{p['type_b']:<4} "
              f"dms {p['ms_gap']:>6} tid {p['tid_a']}/{p['tid_b']} "
              f"fit {p['fit_a']}/{p['fit_b']} states {p['book_states_between']}")
    return pairs


if __name__ == "__main__":
    main(sys.argv[1:])
