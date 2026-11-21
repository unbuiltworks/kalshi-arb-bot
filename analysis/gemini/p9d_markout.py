"""P9-D: markout -- the settlement-free test of whether a family can pay a maker.

    python3 analysis/gemini/p9d_markout.py <pull.json> [spreads.json]

WHY THIS AND NOT P&L. Almost nothing on the live slate carries a
`resolutionSide`, so a settled-position P&L can only be computed for a
handful of families. Markout needs no settlement: it asks what the market did
NEXT to the price we were filled at, which is the whole question a maker
faces.

    we buy YES at b (our bid was hit)   -> markout(h) = mid(t+h) - b
    we buy NO  at 1-a (our ask was hit) -> markout(h) = a - mid(t+h)

and it splits into two terms that must be read separately:

    edge    = half the spread, positive BY CONSTRUCTION -- what we quoted for
    impact  = signed move of the mid from quote time to t+h -- what it cost

    markout = edge + impact

A QUOTE HAS AN AGE, and ignoring it is how this measurement goes wrong. If the
previous print was three days ago, "quote at the last price" models an order
nobody would still have resting. `max_age_s` caps how stale the quote may be;
fills behind a staler quote are excluded and counted, not silently included.

Mid is taken as the last traded price, which is the only mid a trade-only
dataset has; that is a limitation, not an approximation to be forgotten.
Every fill here is HYPOTHETICAL, tier AT_LEVEL, and is never mixed with the
venue's 56 confirmed maker fills.

RAW TRADES, no twin deletion, no capture thinning (P8 audit ed4573b).
"""
import json
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p9d_sequential as S                                     # noqa: E402
import p9d_report as R                                         # noqa: E402

HORIZONS = (0, 10, 30, 60, 300)


def markouts(c, spread, *, max_age_s=60):
    """Re-centred maker quotes, one print of latency, and what happened next.

    Returns (fills, stale) -- `stale` counts fills rejected because the quote
    they imply would have been older than `max_age_s`.
    """
    pr = S.prints_of(c)
    if len(pr) < 3:
        return [], 0
    half = spread / 2
    ts = [int(r[0]) for r in pr]
    px = [F(str(r[2])) for r in pr]
    out, stale = [], 0
    for k in range(1, len(pr)):
        side = str(pr[k][1])
        quote_mid = px[k - 1]
        bid, ask = quote_mid - half, quote_mid + half
        if side == "sell" and px[k] <= bid and F(0) < bid < F(1):
            fill, sgn = bid, 1            # we bought YES at bid
        elif side == "buy" and px[k] >= ask and F(0) < ask < F(1):
            fill, sgn = ask, -1           # we bought NO, i.e. short YES at ask
        else:
            continue
        if ts[k] - ts[k - 1] > max_age_s:
            stale += 1
            continue
        row = {"t": ts[k], "side": ("YES" if sgn > 0 else "NO"),
               "fill": float(fill), "tid": (pr[k][4] if len(pr[k]) > 4 else None),
               "edge": float(sgn * (quote_mid - fill))}
        for h in HORIZONS:
            j = k
            while j + 1 < len(ts) and ts[j + 1] <= ts[k] + h:
                j += 1
            row[f"i{h}"] = float(sgn * (px[j] - quote_mid))
            row[f"m{h}"] = row["edge"] + row[f"i{h}"]
        j = len(px) - 1
        row["mEnd"] = float(sgn * (px[j] - fill))
        out.append(row)
    return out, stale


def main(path, spreads_path=None):
    raw = json.load(open(path))
    spreads = R.load_spreads(spreads_path)
    fams = defaultdict(list)
    for c in raw:
        t = R.tape_stats(c)
        if t is None:
            continue
        c["_tape"] = t
        fams[S.family(c)].append(c)

    print(f"MARKOUT BY FAMILY -- mean cents per hypothetical maker fill "
          f"(tier AT_LEVEL)\nRAW trades, re-centred quote, one print of latency. "
          f"m0 is half the spread BY CONSTRUCTION.")
    print(f"\n{'category':>13} {'subcategory':>20} {'fills':>6} {'stale':>6} "
          f"{'spread':>7} {'edge':>7} {'imp0':>7} {'imp30':>7} {'imp60':>7} "
          f"{'imp300':>7} {'m60':>8} {'mEnd':>8} {'verdict':>10}")
    rows = []
    for k, cs in fams.items():
        sp = spreads.get(k, R.DEFAULT_SPREAD)
        fills, stale = [], 0
        for c in cs:
            f2, st = markouts(c, sp)
            fills += f2
            stale += st
        if len(fills) < 30:
            continue
        agg = {f"m{h}": sum(f[f"m{h}"] for f in fills) / len(fills)
               for h in HORIZONS}
        agg.update({f"i{h}": sum(f[f"i{h}"] for f in fills) / len(fills)
                    for h in HORIZONS})
        agg["edge"] = sum(f["edge"] for f in fills) / len(fills)
        agg["mEnd"] = sum(f["mEnd"] for f in fills) / len(fills)
        rows.append((agg["m60"], k, sp, len(fills), stale, agg))
    for _v, k, sp, n, stale, a in sorted(rows, reverse=True):
        verdict = ("PAID" if a["m60"] > 0.002 else
                   ("MARGINAL" if a["m60"] > -0.002 else "ADVERSE"))
        print(f"{k[0]:>13} {str(k[1])[:20]:>20} {n:>6} {stale:>6} "
              f"{float(sp):>7.3f} {a['edge']:>7.3f} {a['i0']:>7.3f} "
              f"{a['i30']:>7.3f} {a['i60']:>7.3f} {a['i300']:>7.3f} "
              f"{a['m60']:>8.3f} {a['mEnd']:>8.3f} {verdict:>10}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
