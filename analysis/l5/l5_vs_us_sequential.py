#!/usr/bin/env python3
"""L5's actual sequential decisions vs ours, at the same four junctures.

Source: analysis/l5/l5_all_trades.csv (71,984 normalized fills) restricted
to BTC 15-minute up/down windows, which is the series our engine trades.

Prices are per-outcome, so a matched pair's cost is up_price + dn_price in
own terms and a pair below 1.00 is locked profit. Shares are contracts.
"""
import csv, collections, statistics as st
from decimal import Decimal as D

ROWS = []
with open("analysis/l5/l5_all_trades.csv") as fh:
    for x in csv.DictReader(fh):
        if x["asset"] != "BTC" or x["window_minutes"] != "15":
            continue
        if x["side"] != "BUY":
            continue
        try:
            ROWS.append(dict(
                w=x["market_slug"],
                t=float(x["seconds_from_window_start"]),
                up=(x["outcome"] == "Up"),
                px=D(x["price"]).quantize(D("0.0001")),
                sh=D(x["shares"]),
                usd=D(x["dollars"])))
        except Exception:
            continue
ROWS.sort(key=lambda r: (r["w"], r["t"]))
byw = collections.defaultdict(list)
for r in ROWS:
    byw[r["w"]].append(r)

print(f"L5 BTC 15m: {len(ROWS)} fills across {len(byw)} windows\n")

def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))]

# ---------- per-window sequential reconstruction ----------
trades_pw, clip, final_imb, pair_costs = [], [], [], []
first_pair_t, after_first_pair, strand_windows = [], [], 0
late_complete, residual_frac = [], []
n_up_first = 0

for w, rs in byw.items():
    trades_pw.append(len(rs))
    up = dn = D(0)
    up_cost = dn_cost = D(0)
    pair_t = None
    after = 0
    for r in rs:
        clip.append(float(r["sh"]))
        if r["up"]:
            up += r["sh"]; up_cost += r["sh"] * r["px"]
        else:
            dn += r["sh"]; dn_cost += r["sh"] * r["px"]
        matched = min(up, dn)
        if pair_t is None and matched > 0:
            pair_t = r["t"]
            if rs[0]["up"]:
                n_up_first += 1
        elif pair_t is not None:
            after += 1
    if pair_t is not None:
        first_pair_t.append(pair_t)
        after_first_pair.append(after)
    matched = min(up, dn)
    imb = abs(up - dn)
    tot = up + dn
    final_imb.append(float(imb))
    if tot > 0:
        residual_frac.append(float(imb / tot))
    if imb > 0:
        strand_windows += 1
    if matched > 0 and up > 0 and dn > 0:
        # average own cost of the matched portion, pro-rata like our engine
        pc = (up_cost / up + dn_cost / dn)
        pair_costs.append(float(pc))

print("=" * 76)
print("SCALE AND CLIP  (the first thing that differs)")
print("=" * 76)
print(f"  fills per window        p10 {pct(trades_pw,.1):6.0f}  p50 {pct(trades_pw,.5):6.0f}"
      f"  p90 {pct(trades_pw,.9):6.0f}  max {max(trades_pw):6.0f}")
print(f"  contracts per fill      p10 {pct(clip,.1):6.3f}  p50 {pct(clip,.5):6.3f}"
      f"  p90 {pct(clip,.9):6.3f}  max {max(clip):6.1f}")
print(f"  OUR engine: 2-3 fills per window, 0.96-2.18 contracts per fill")

print()
print("=" * 76)
print("JUNCTURE 1: WHEN THE FIRST PAIR COMPLETES")
print("=" * 76)
print(f"  windows reaching a matched pair: {len(first_pair_t)} of {len(byw)}"
      f"  ({100.0*len(first_pair_t)/len(byw):.1f}%)")
print(f"  seconds into window at first pair   p10 {pct(first_pair_t,.1):6.0f}"
      f"  p50 {pct(first_pair_t,.5):6.0f}  p90 {pct(first_pair_t,.9):6.0f}")
print(f"  OUR first pair: 2 of 10 windows; 33.6s and 20.8s after the first leg")

print()
print("=" * 76)
print("JUNCTURE 2: AFTER THE FIRST PAIR -- DOES L5 KEEP GOING?")
print("=" * 76)
print(f"  further fills after the first pair  p10 {pct(after_first_pair,.1):6.0f}"
      f"  p50 {pct(after_first_pair,.5):6.0f}  p90 {pct(after_first_pair,.9):6.0f}"
      f"  max {max(after_first_pair):6.0f}")
zero = sum(1 for a in after_first_pair if a == 0)
print(f"  windows that STOP after the first pair: {zero} of {len(after_first_pair)}"
      f"  ({100.0*zero/max(1,len(after_first_pair)):.1f}%)")
print(f"  OUR engine: opened 1 further leg in each of 2 windows, 0 of 3 completed")

print()
print("=" * 76)
print("JUNCTURE 3: STRANDED INVENTORY AT WINDOW END")
print("=" * 76)
print(f"  windows ending with ANY imbalance: {strand_windows} of {len(byw)}"
      f"  ({100.0*strand_windows/len(byw):.1f}%)")
print(f"  |imbalance| contracts              p50 {pct(final_imb,.5):7.3f}"
      f"  p90 {pct(final_imb,.9):7.3f}  max {max(final_imb):7.1f}")
print(f"  imbalance as a FRACTION of window volume  p50 {pct(residual_frac,.5):6.3f}"
      f"  p90 {pct(residual_frac,.9):6.3f}")
print(f"  OUR engine: 6 of 10 windows stranded; residual fraction ~0.45-1.00")

print()
print("=" * 76)
print("EXECUTION ECONOMICS: L5's ACHIEVED PAIR COST")
print("=" * 76)
print(f"  matched pair cost (pro-rata)  p10 {pct(pair_costs,.1):6.4f}"
      f"  p50 {pct(pair_costs,.5):6.4f}  p90 {pct(pair_costs,.9):6.4f}")
below = sum(1 for p in pair_costs if p < 1.0)
print(f"  windows whose matched pair cost < 1.00: {below} of {len(pair_costs)}"
      f"  ({100.0*below/max(1,len(pair_costs)):.1f}%)")
b96 = sum(1 for p in pair_costs if p <= 0.96)
print(f"  ... and <= 0.96 (our cap):             {b96} of {len(pair_costs)}"
      f"  ({100.0*b96/max(1,len(pair_costs)):.1f}%)")
print(f"  OUR achieved pair costs: 0.82, 0.85, 0.93, 0.94, 0.95, 0.96")
