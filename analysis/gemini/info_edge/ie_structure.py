"""Is there predictive structure beyond sign(distance)? DEV-ONLY DIAGNOSTIC.

Reads ONLY the frozen 48-window DEV cohort. Prospective windows untouched.
This is a mechanism diagnostic, not a search for the most profitable rule, and
no rule is fitted or frozen from it.

TWO QUESTIONS, KEPT SEPARATE
  Q1 can richer BTC-state variables predict SETTLEMENT better than sign alone?
  Q2 where they can, was the predicted side AVAILABLE below that probability?

DISCOVERY NORMALIZATION CHANGED, per instruction: economics are reported per
CONTRACT (edge per dollar at risk), not sized to a $5 target. Sizing for $5 at
96c means risking ~$120, which is a terrible normalizer while we are still
asking whether an edge exists at all.

MULTIPLICITY IS CALIBRATED, NOT IGNORED
  ~11 variables x 9 TTEs is ~99 cells on 48 windows. At n=48 (24/24) the
  standard error of an AUC is roughly 0.085, so several cells will look
  predictive by chance. Every reported AUC is therefore compared against a
  PERMUTATION NULL: settlement labels are shuffled across windows and the
  WHOLE grid is recomputed, 2000 times, recording the max |AUC-0.5| achieved
  anywhere in the grid. An observed cell only counts as structure if it beats
  what the best cell achieves under pure noise.

At a fixed TTE each window contributes exactly ONE observation, so there is no
within-window correlation to cluster over -- n is simply the number of windows.
"""
from __future__ import annotations

import bisect
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TTES = (120, 90, 60, 45, 30, 20, 15, 10, 5)
NPERM = 2000
random.seed(20261003)

VARS = ["dist", "dist_norm", "ret_5s", "ret_15s", "ret_30s", "ret_60s",
        "accel", "crossings_signed", "frac_above_c", "max_exc_signed",
        "reversal"]


def ap(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def auc(xs, ys):
    """AUC of x predicting binary y. 0.5 = no information."""
    pos = [x for x, y in zip(xs, ys) if y]
    neg = [x for x, y in zip(xs, ys) if not y]
    if not pos or not neg:
        return None
    wins = ties = 0
    for a in pos:
        for b in neg:
            if a > b:
                wins += 1
            elif a == b:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def build():
    dev = json.loads(Path("analysis/gemini/info_edge/DEV_COHORT_FROZEN.json").read_text())
    keep = set(dev["tickers"])
    settled, expiry, cb = {}, {}, []
    ev = defaultdict(list)
    for f in sorted(Path("data/gemini_infoedge").glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if r.get("ticker") in keep:
                    settled[r["ticker"]] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m or m.group(1) not in keep:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll") and r.get("expiry"):
                expiry[tk] = datetime.fromisoformat(
                    r["expiry"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot"):
                ev[tk].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    rows = []
    for tk, s in settled.items():
        E = expiry.get(tk)
        if E is None:
            continue
        try:
            strike = float(s["strike"])
        except Exception:
            continue
        up_won = s["resolution_side"] == "yes"
        start = E - 300.0
        evs = sorted(ev.get(tk, []), key=lambda r: r["t_ns"])
        bids, asks, seeded, last_u = {}, {}, False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        state = {}
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                bids, asks = {}, {}
                ap(bids, r.get("bids"))
                ap(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                ap(bids, r.get("b"))
                ap(asks, r.get("a"))
                last_u = u
            while wi < len(want) and (E - t) <= want[wi]:
                state[want[wi]] = (dict(bids), dict(asks), t)
                wi += 1
        for tte in TTES:
            st = state.get(tte)
            if st is None:
                continue
            b, a, t = st
            c = at(t)
            if c is None:
                continue
            # path features over the window so far, causal
            i0 = bisect.bisect_left(cbt, max(start, t - 300.0))
            i1 = bisect.bisect_right(cbt, t)
            seg = [x[1] for x in cb[i0:i1]]
            segt = [x[0] for x in cb[i0:i1]]
            if len(seg) < 10:
                continue
            d = [seg[i + 1] - seg[i] for i in range(len(seg) - 1)]
            sigma = math.sqrt(sum(x * x for x in d) / len(d)) * math.sqrt(
                len(d) / max(1e-9, segt[-1] - segt[0]))
            dist = c - strike
            above = [1 if p > strike else 0 for p in seg]
            crossings = sum(1 for i in range(len(above) - 1)
                            if above[i] != above[i + 1])
            frac_above = sum(above) / len(above)
            devs = [p - strike for p in seg]
            max_exc = max(devs, key=abs)
            ret = {}
            for h in (5, 15, 30, 60):
                j = bisect.bisect_right(cbt, t - h) - 1
                ret[h] = (c - cb[j][1]) if j >= 0 else None
            j5 = bisect.bisect_right(cbt, t - 5) - 1
            j10 = bisect.bisect_right(cbt, t - 10) - 1
            accel = ((ret[5] - (cb[j5][1] - cb[j10][1]))
                     if (ret[5] is not None and j5 >= 0 and j10 >= 0) else None)
            bid = max(b) if b else None
            ask = min(a) if a else None
            rows.append({
                "ticker": tk, "tte": tte, "up_won": up_won, "strike": strike,
                "coinbase": c, "bid": bid, "ask": ask,
                "ask_sz": (a[ask] if ask is not None else None),
                "bid_sz": (b[bid] if bid is not None else None),
                "dist": dist,
                "dist_norm": (dist / (sigma * math.sqrt(tte))
                              if sigma > 0 else None),
                "ret_5s": ret[5], "ret_15s": ret[15],
                "ret_30s": ret[30], "ret_60s": ret[60],
                "accel": accel,
                # signed so that a POSITIVE value should favour UP
                "crossings_signed": (-crossings if dist > 0 else crossings),
                "frac_above_c": frac_above - 0.5,
                "max_exc_signed": max_exc,
                "reversal": dist - max_exc,
                "sigma": sigma,
            })
    return rows


def main():
    rows = build()
    nw = len({r["ticker"] for r in rows})
    print("=" * 86)
    print("Q1: IS THERE PREDICTIVE STRUCTURE BEYOND sign(distance)?  DEV ONLY")
    print("=" * 86)
    print(f"DEV windows: {nw}   observations: {len(rows)}")
    print(f"permutation null: {NPERM} shuffles of the settlement labels\n")

    grid = {}
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte]
        if len(g) < 20:
            continue
        ys = [r["up_won"] for r in g]
        for v in VARS:
            xs = [r.get(v) for r in g]
            if any(x is None for x in xs):
                continue
            grid[(v, tte)] = (auc(xs, ys), len(g))
    # baseline: sign(dist) accuracy and AUC
    print(f"  {'variable':>18s} " + " ".join(f"{t:>6d}" for t in TTES))
    print("  " + "-" * (18 + 7 * len(TTES)))
    for v in VARS:
        line = f"  {v:>18s} "
        for t in TTES:
            a = grid.get((v, t))
            line += f"{a[0]:6.3f} " if a else "     - "
        print(line)
    print("\n  (AUC 0.5 = no information. n per cell is the window count.)")

    # ---- permutation null over the WHOLE grid ----
    bywin = defaultdict(dict)
    for r in rows:
        bywin[r["ticker"]][r["tte"]] = r
    wins = sorted(bywin)
    truth = [bywin[w][list(bywin[w])[0]]["up_won"] for w in wins]
    best_obs = max(abs(a[0] - 0.5) for a in grid.values())
    null_max = []
    for _ in range(NPERM):
        perm = truth[:]
        random.shuffle(perm)
        lab = dict(zip(wins, perm))
        m = 0.0
        for (v, tte) in grid:
            g = [r for r in rows if r["tte"] == tte and r.get(v) is not None]
            if not g:
                continue
            xs = [r[v] for r in g]
            ys = [lab[r["ticker"]] for r in g]
            a = auc(xs, ys)
            if a is not None:
                m = max(m, abs(a - 0.5))
        null_max.append(m)
    null_max.sort()
    p95 = null_max[int(0.95 * len(null_max))]
    p99 = null_max[int(0.99 * len(null_max))]
    pval = sum(1 for x in null_max if x >= best_obs) / len(null_max)
    print("\n" + "=" * 86)
    print("MULTIPLICITY CALIBRATION")
    print("=" * 86)
    print(f"  cells tested                     : {len(grid)}")
    print(f"  best observed |AUC - 0.5|        : {best_obs:.3f}")
    print(f"  null max |AUC-0.5|: p50={null_max[len(null_max)//2]:.3f} "
          f"p95={p95:.3f} p99={p99:.3f}")
    print(f"  p-value of the best cell         : {pval:.3f}")
    if best_obs > p95:
        print("  => the best cell EXCEEDS the 95th percentile of pure noise.")
    else:
        print("  => the best cell is WITHIN what pure noise produces across a")
        print("     grid this size. NO STRUCTURE IS ESTABLISHED.")
    top = sorted(grid.items(), key=lambda kv: -abs(kv[1][0] - 0.5))[:8]
    print("\n  strongest cells (for the record, noise-calibrated above):")
    for (v, t), (a, n) in top:
        flag = "  <= beats null p95" if abs(a - 0.5) > p95 else ""
        print(f"    {v:>18s} @ T-{t:<4d} AUC={a:.3f}  n={n}{flag}")
    Path("analysis/gemini/info_edge/ie_structure_grid.json").write_text(
        json.dumps({f"{v}|{t}": {"auc": a, "n": n}
                    for (v, t), (a, n) in grid.items()}, indent=1))
    return rows, grid, p95


if __name__ == "__main__":
    main()


def q2(rows, p95):
    """Q2: where the state IS informative, was the predicted side available
    BELOW its empirical conditional settlement probability?

    Conditional probability is estimated on DEV by |dist| bucket -- the same
    data, so this is DESCRIPTIVE and deliberately GENEROUS to the hypothesis.
    If the predicted side is not cheap even against an in-sample (optimistic)
    probability, it will not be cheap against an honest out-of-sample one.
    """
    print("\n" + "=" * 86)
    print("Q2: WHERE THE STATE IS INFORMATIVE, WAS THE PREDICTED SIDE CHEAP?")
    print("=" * 86)
    print("  Conditional P is estimated IN-SAMPLE on DEV, i.e. deliberately")
    print("  generous. A side that is not cheap against an optimistic P cannot")
    print("  be cheap against an honest one.\n")
    BK = [("|d|<5", 0, 5), ("5-15", 5, 15), ("15-30", 15, 30),
          ("30-60", 30, 60), ("|d|>60", 60, 1e9)]
    for tte in (120, 90, 60, 45, 30, 15):
        g = [r for r in rows if r["tte"] == tte]
        if len(g) < 20:
            continue
        print(f"  TTE = {tte}s   (n={len(g)} windows)")
        print(f"    {'|dist|':>8s} {'n':>4s} {'P(pred correct)':>16s} "
              f"{'avail':>6s} {'avail%':>8s} {'px_p50':>8s} {'P - px':>9s} "
              f"{'sz_p50':>8s}")
        for name, lo, hi in BK:
            b = [r for r in g if lo <= abs(r["dist"]) < hi]
            if len(b) < 3:
                continue
            # predicted side = sign of dist; correct?
            corr = [((r["dist"] > 0) == r["up_won"]) for r in b]
            P = sum(corr) / len(corr)
            av = []
            for r in b:
                if r["dist"] > 0:
                    px, sz = r["ask"], r["ask_sz"]
                else:
                    px = (1 - r["bid"]) if r["bid"] is not None else None
                    sz = r["bid_sz"]
                if px is not None:
                    av.append((px, sz))
            pxs = sorted(x[0] for x in av)
            szs = sorted(x[1] for x in av if x[1] is not None)
            px50 = pxs[len(pxs) // 2] if pxs else None
            sz50 = szs[len(szs) // 2] if szs else None
            edge = (P - px50) if px50 is not None else None
            f = lambda v, n=3: "-" if v is None else f"{v:.{n}f}"
            mark = ""
            if edge is not None and edge > 0:
                mark = "   <== CHEAP"
            print(f"    {name:>8s} {len(b):4d} {P:16.3f} {len(av):6d} "
                  f"{len(av)/len(b)*100:7.1f}% {f(px50):>8s} "
                  f"{f(edge):>9s} {f(sz50,1):>8s}{mark}")
        print()


if __name__ == "__main__":
    pass
