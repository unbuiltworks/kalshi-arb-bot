"""P10-E: a second decoder, written independently, plus a sample to eyeball.

    python3 analysis/gemini/p10e_audit_decode.py <capture.jsonl> [max_stale_s]

THIS FILE IMPORTS NOTHING FROM `p10b_decode`. It reads the capture itself and
decides by a DIFFERENT RULE, so agreement between the two is evidence and
disagreement is a finding.

  p10b  INTERVAL CONTAINMENT -- is the price inside [bestBid, bestAsk] with a
        tick of slack, and is its mirror outside?
  p10e  NEAREST TOUCH WITH A MARGIN -- how far is the price from the closer of
        the two touch prices, how far is its mirror, and does one beat the
        other by more than `margin`?

The two rules disagree in exactly the cases that matter: a price outside the
book but close to a touch is AMBIGUOUS to p10b and decodable to p10e; a price
comfortably inside a wide book is decodable to neither. Reporting where they
diverge is the point of having two.

It also prints a STRATIFIED RANDOM SAMPLE -- by family, by label, by book
width -- with the raw numbers a human needs to check a row by hand: the
trade's price, its mirror, the book before and after, the gaps in seconds, and
the two decoders' verdicts.
"""
import json
import random
import sys
from collections import defaultdict
from fractions import Fraction as F

MARGIN = F(2, 100)          # the mirror must be at least this much worse


def read(path):
    books, trades = defaultdict(list), []
    with open(path) as fh:
        for ln in fh:
            try:
                d = json.loads(ln)
            except Exception:                                  # noqa: BLE001
                continue
            if d.get("kind") == "book" and d.get("bid") and d.get("ask"):
                books[d["symbol"]].append((d["t_recv"], F(str(d["bid"])),
                                           F(str(d["ask"]))))
            elif d.get("kind") == "trade":
                trades.append(d)
    for v in books.values():
        v.sort()
    return books, trades


def touch_distance(p, bid, ask):
    """0 if the price IS a touch; otherwise how far from the closer touch."""
    return min(abs(p - bid), abs(p - ask))


def decode(trade, books, max_stale_s):
    sym = trade["symbol"]
    t = (trade.get("tms") or 0) / 1000.0
    rows = books.get(sym) or []
    if not rows:
        return {"label": "NO_BOOK"}
    if t < rows[0][0]:
        return {"label": "PRE_CAPTURE"}
    cand = [r for r in rows if abs(r[0] - t) <= max_stale_s]
    if not cand:
        return {"label": "STALE"}
    p = F(str(trade["price"]))
    q = F(1) - p
    dp = min(touch_distance(p, b, a) for _t, b, a in cand)
    dq = min(touch_distance(q, b, a) for _t, b, a in cand)
    if dp + MARGIN <= dq:
        lab = "H1"
    elif dq + MARGIN <= dp:
        lab = "H2"
    else:
        lab = "AMBIGUOUS"
    width = min(float(a - b) for _t, b, a in cand)
    return {"label": lab, "d_own": float(dp), "d_mirror": float(dq),
            "book_width": width}


def main(path, max_stale_s=5.0):
    max_stale_s = float(max_stale_s)
    books, trades = read(path)
    out = []
    for tr in trades:
        r = decode(tr, books, max_stale_s)
        r.update({"tid": tr.get("tid"), "symbol": tr["symbol"],
                  "category": tr.get("category"),
                  "subcategory": tr.get("subcategory"),
                  "price": tr.get("price"), "type": tr.get("type"),
                  "amount": tr.get("amount"), "tms": tr.get("tms")})
        out.append(r)

    by = defaultdict(lambda: defaultdict(int))
    for r in out:
        by[(r.get("category") or "?", r.get("subcategory") or "?")][r["label"]] += 1
    print(f"INDEPENDENT DECODER (nearest touch, margin {float(MARGIN):.2f}) "
          f"-- {path}")
    print(f"{'category':>12} {'subcategory':>20} {'n':>5} {'H1':>5} {'H2':>5} "
          f"{'AMB':>5} {'STALE':>6} {'pre':>5} {'decoded/obs':>12}")
    for k, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        obs = n - c["PRE_CAPTURE"] - c["NO_BOOK"]
        dec = c["H1"] + c["H2"]
        print(f"{k[0]:>12} {str(k[1])[:20]:>20} {n:>5} {c['H1']:>5} {c['H2']:>5} "
              f"{c['AMBIGUOUS']:>5} {c['STALE']:>6} {c['PRE_CAPTURE']:>5} "
              f"{(dec / obs if obs else 0):>11.1%}")
    return out


def sample(path, rows_b, rows_e, k=20, seed=0):
    """A stratified random sample, printed with everything a human needs."""
    idx = {(r["symbol"], r["tid"]): r for r in rows_e}
    strata = defaultdict(list)
    for r in rows_b:
        key = (r.get("subcategory") or "?", r.get("label"))
        strata[key].append(r)
    rng = random.Random(seed)
    print(f"\nSTRATIFIED SAMPLE FOR MANUAL INSPECTION "
          f"({k} rows, seed {seed})")
    print(f"{'symbol':>30} {'price':>7} {'mirror':>7} {'type':>5} "
          f"{'book before':>15} {'book after':>15} {'age-':>6} {'age+':>6} "
          f"{'p10b':>18} {'p10e':>10}")
    keys = sorted(strata)
    picked = []
    while len(picked) < k and keys:
        for key in list(keys):
            if not strata[key]:
                keys.remove(key)
                continue
            picked.append(strata[key].pop(rng.randrange(len(strata[key]))))
            if len(picked) >= k:
                break
    for r in picked:
        e = idx.get((r["symbol"], r["tid"]), {})
        p = F(str(r["price"]))
        bb = "-" if not r.get("book_prev") else f"{r['book_prev'][0]}/{r['book_prev'][1]}"
        ba = "-" if not r.get("book_next") else f"{r['book_next'][0]}/{r['book_next'][1]}"
        ap = "-" if r.get("age_prev_s") is None else f"{r['age_prev_s']:.1f}"
        an = "-" if r.get("age_next_s") is None else f"{r['age_next_s']:.1f}"
        print(f"{r['symbol'][:30]:>30} {r['price']:>7} "
              f"{str(float(1 - p)):>7} {str(r.get('type')):>5} {bb:>15} {ba:>15} "
              f"{ap:>6} {an:>6} {str(r.get('label')):>18} "
              f"{str(e.get('label')):>10}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
