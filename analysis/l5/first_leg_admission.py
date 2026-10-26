#!/usr/bin/env python3
"""Inventory-aware first-leg admission, tested against the current engine.

THESIS. L5's advantage may come from choosing first-leg entries by the
future probability and economics of a complementary fill, while managing
inventory continuously.

METHOD. `analysis/l5/l5_all_trades.csv` gives L5's fills but no order book,
so L5's own fills are the executable price series: a fill at p is proof that
p traded. That makes complement executability measurable without inventing a
book. It is an UPPER BOUND on what we could have got, because our maker order
would have queued behind L5's -- stated here and carried into every number
below rather than quietly assumed away.

A first-leg decision is a fill that INCREASES |imbalance|. For each one, the
label is whether a complement fill later in the same window, at or before the
completion cutoff, would have paired it at or under the cap.

Features are decision-time only: elapsed seconds, the leg's own price,
inventory before the fill, the imbalance-to-matched ratio, volume so far, and
time left to the entry cutoff. No feature reads the future.
"""
import csv, collections, json, random
from decimal import Decimal as D

CAP = D("0.96")
ENTRY_CUTOFF = 600.0
COMPLETE_CUTOFF = 720.0
FLOOR_NOW = D("0.20")          # the live first-leg floor our engine applies

def load():
    byw = collections.defaultdict(list)
    with open("analysis/l5/l5_all_trades.csv") as fh:
        for x in csv.DictReader(fh):
            if (x["asset"] != "BTC" or x["window_minutes"] != "15"
                    or x["side"] != "BUY"):
                continue
            try:
                byw[x["market_slug"]].append(dict(
                    t=float(x["seconds_from_window_start"]),
                    up=(x["outcome"] == "Up"),
                    px=D(x["price"]).quantize(D("0.0001")),
                    sh=D(x["shares"])))
            except Exception:
                continue
    for w in byw:
        byw[w].sort(key=lambda r: r["t"])
    return byw

def decisions(rs):
    """Every first-leg-like fill in one window, with causal features."""
    out = []
    up = dn = D(0)
    vol = D(0)
    for i, r in enumerate(rs):
        imb_before = up - dn
        matched_before = min(up, dn)
        # does this fill increase |imbalance|? that is an OPENING
        nu, nd = (up + r["sh"], dn) if r["up"] else (up, dn + r["sh"])
        opens = abs(nu - nd) > abs(imb_before)
        if opens and r["t"] <= ENTRY_CUTOFF:
            # complement executable later, at or under the cap?
            comp_up = not r["up"]
            best = None
            for s in rs[i + 1:]:
                if s["t"] > COMPLETE_CUTOFF:
                    break
                if s["up"] != comp_up:
                    continue
                pair = r["px"] + s["px"]
                if best is None or pair < best[0]:
                    best = (pair, s["t"], s["px"], s["sh"])
            out.append(dict(
                t=r["t"], own=r["px"], sh=r["sh"], up=r["up"],
                imb_before=imb_before, matched_before=matched_before,
                ratio=(float(abs(imb_before) / matched_before)
                       if matched_before > 0 else None),
                vol_before=vol,
                runway=ENTRY_CUTOFF - r["t"],
                best_pair=(best[0] if best else None),
                comp_t=(best[1] if best else None),
                paired=bool(best and best[0] <= CAP)))
        up, dn = nu, nd
        vol += r["sh"]
    return out

def main():
    byw = load()
    wins = sorted(byw)
    random.Random(20261026).shuffle(wins)
    cut = int(0.6 * len(wins))
    train, held = set(wins[:cut]), set(wins[cut:])
    print(f"windows: {len(wins)}  train {len(train)}  held-out {len(held)}\n")

    rows = {}
    for w in wins:
        rows[w] = decisions(byw[w])
    n = sum(len(v) for v in rows.values())
    print(f"first-leg decisions (openings at or before T+{ENTRY_CUTOFF:.0f}s): {n}")
    allr = [d for w in train for d in rows[w]]
    paired = sum(1 for d in allr if d["paired"])
    print(f"  train: {len(allr)}; complement paired at or under {CAP}: "
          f"{paired} ({100.0*paired/max(1,len(allr)):.1f}%)")

    # what our CURRENT engine admits: own price at or above the live floor
    cur = [d for d in allr if d["own"] >= FLOOR_NOW]
    cur_p = sum(1 for d in cur if d["paired"])
    print(f"\ncurrent engine rule (own >= {FLOOR_NOW}):")
    print(f"  admits {len(cur)} of {len(allr)} ({100.0*len(cur)/len(allr):.1f}%), "
          f"paired {cur_p} ({100.0*cur_p/max(1,len(cur)):.1f}%)")

    # does pairing depend on the features the thesis names?
    print("\npair rate by DECISION-TIME feature (train split):")
    def bucket(name, keyfn, buckets):
        print(f"  {name}")
        for lo, hi in buckets:
            sel = [d for d in allr if keyfn(d) is not None
                   and lo <= keyfn(d) < hi]
            if not sel:
                continue
            p = sum(1 for d in sel if d["paired"])
            print(f"    {lo:>7} - {hi:<7} n={len(sel):6d}  paired "
                  f"{100.0*p/len(sel):5.1f}%")
    bucket("own price of the first leg", lambda d: float(d["own"]),
           [(0.0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,1.01)])
    bucket("elapsed seconds", lambda d: d["t"],
           [(0,60),(60,120),(120,240),(240,360),(360,480),(480,601)])
    bucket("|imbalance| / matched before", lambda d: d["ratio"],
           [(0,.1),(.1,.25),(.25,.5),(.5,1.0),(1.0,2.0),(2.0,1e9)])
    json.dump({w: len(rows[w]) for w in wins}, open("/dev/null", "w"))

if __name__ == "__main__":
    main()
