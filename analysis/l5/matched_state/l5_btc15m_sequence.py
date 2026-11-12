#!/usr/bin/env python3
"""L5's BTC15m SEQUENTIAL behaviour, measured from his venue fills only.

OBSERVABLE FROM FILLS: when each fill happened, its side, price and size;
the running inventory; matched pairs; residual at settlement (his REDEEM).
NOT OBSERVABLE: resting orders, quote placement, cancellations, repricing
that never filled. Nothing below infers them.

Per window:
  gaps            seconds between successive fills
  t_first_opp     seconds from first fill to first fill on the other side
  alternation     share of consecutive fills that switch side
  run_len         mean length of same-side runs
  imb_max_frac    max |UP-DOWN| / contracts bought so far (after 3 fills)
  imb_time_frac   time-weighted mean |UP-DOWN| / running contracts
  cycles          times the running imbalance returns to <= one median fill
                  size after having exceeded it (an inventory turnover)
  residual_frac   |UP-DOWN| / bought at settlement
  same_side_add_after_drop   share of same-side fills priced BELOW that
                  side's previous fill (averaging down) vs above

    python3 l5_btc15m_sequence.py ACTIVITY.jsonl... [--out OUT.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics as st
import sys


def load(paths):
    by = collections.defaultdict(list)
    red = collections.defaultdict(float)
    for p in paths:
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            s = str(r.get("slug") or "")
            if not s.startswith("btc-updown-15m-"):
                continue
            if r.get("type") == "TRADE" and r.get("side") == "BUY":
                by[s].append(r)
            elif r.get("type") == "REDEEM":
                red[s] += float(r.get("usdcSize") or 0)
    return by, red


def window_metrics(slug, fills, redeemed):
    ws = int(slug.rsplit("-", 1)[1])
    fills = sorted(fills, key=lambda r: (r["timestamp"], r.get("transactionHash", "")))
    t = [r["timestamp"] - ws for r in fills]
    side = [r["outcome"] for r in fills]
    size = [float(r["size"]) for r in fills]
    px = [float(r["price"]) for r in fills]
    gaps = [b - a for a, b in zip(t, t[1:])]
    first_opp = next((t[i] - t[0] for i in range(len(side)) if side[i] != side[0]), None)
    switches = sum(1 for a, b in zip(side, side[1:]) if a != b)
    runs, cur = [], 1
    for a, b in zip(side, side[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur); cur = 1
    runs.append(cur)
    med = st.median(size)
    up = dn = 0.0
    imb_max = 0.0
    cycles = 0
    above = False
    tw = 0.0; tw_den = 0.0
    last_px = {}
    below = above_px = 0
    for i, (tt, s, q, p) in enumerate(zip(t, side, size, px)):
        if s in last_px:
            if p < last_px[s]:
                below += 1
            elif p > last_px[s]:
                above_px += 1
        last_px[s] = p
        if s == "Up":
            up += q
        else:
            dn += q
        imb = abs(up - dn)
        if i >= 2:
            imb_max = max(imb_max, imb / (up + dn))
        if imb > med:
            above = True
        elif above:
            cycles += 1; above = False
        if i + 1 < len(t):
            dt_ = t[i + 1] - tt
            tw += dt_ * imb / (up + dn); tw_den += dt_
    win = None
    if redeemed:
        win = "Up" if abs(redeemed - up) < abs(redeemed - dn) else "Down"
    return {"slug": slug, "fills": len(fills), "first_t": t[0], "last_t": t[-1],
            "gap_median": st.median(gaps) if gaps else None,
            "t_first_opp": first_opp,
            "alternation": switches / (len(side) - 1) if len(side) > 1 else None,
            "run_len_mean": st.mean(runs),
            "imb_max_frac": round(imb_max, 4),
            "imb_time_frac": round(tw / tw_den, 4) if tw_den else None,
            "cycles": cycles, "median_fill_size": med,
            "bought": up + dn, "matched": min(up, dn),
            "residual_frac": abs(up - dn) / (up + dn),
            "same_side_below_prev": below, "same_side_above_prev": above_px,
            "winner": win}


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return v[int(p * (len(v) - 1))] if v else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("activity", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    by, red = load(a.activity)
    rows = [window_metrics(s, f, red.get(s)) for s, f in sorted(by.items()) if len(f) >= 2]
    keys = ["fills", "first_t", "last_t", "gap_median", "t_first_opp", "alternation",
            "run_len_mean", "imb_max_frac", "imb_time_frac", "cycles",
            "median_fill_size", "bought", "matched", "residual_frac"]
    summ = {k: {"p10": q([r[k] for r in rows], 0.1), "median": q([r[k] for r in rows], 0.5),
                "p90": q([r[k] for r in rows], 0.9)} for k in keys}
    below = sum(r["same_side_below_prev"] for r in rows)
    abovep = sum(r["same_side_above_prev"] for r in rows)
    out = {"label": "L5 venue fills only (Polymarket international); resting/cancelled orders unobserved",
           "windows": len(rows), "summary": summ,
           "same_side_repeat_price_vs_previous": {"below": below, "above": abovep,
                                                  "share_below": round(below / max(1, below + abovep), 3)}}
    print(json.dumps(out, indent=1))
    if a.out:
        json.dump({"summary": out, "windows": rows}, open(a.out, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
