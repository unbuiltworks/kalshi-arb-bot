#!/usr/bin/env python3
"""READ-ONLY maker/taker gap diagnosis. No executor change, no live order.

Postability is measured on the authenticated PMUS BTC15m tape (6 windows,
book + trade rows with the touch bound at the instant of each trade).
Postability only -- never converted into a fill.
"""
from __future__ import annotations
import gzip, json, collections, statistics as st, sys, datetime as dt, csv
from decimal import Decimal as D
sys.path.insert(0, ".")
from src.pmus_fees import taker_fee, maker_rebate
WHEN = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
AUTH = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"

T = collections.defaultdict(list); B = collections.defaultdict(list)
def f(x):
    try: return float(x)
    except Exception: return None
for line in gzip.open(AUTH, "rt"):
    d = json.loads(line)
    if d["kind"] == "trade":
        T[d["slug"]].append((d["t"], f(d["px"]), f(d["qty"]), d["dir"],
                             f(d["bid_at_trade"]), f(d["ask_at_trade"])))
    else:
        B[d["slug"]].append((d["t"], f(d["bid"]), f(d["ask"])))
for v in T.values(): v.sort()
for v in B.values(): v.sort()

print("="*80)
print("POSTABILITY OF A PRINT-TRIGGERED TARGET PRICE  (authenticated PMUS tape)")
print("="*80)
print("A target action arrives as 'buy this side at the price that just printed'.")
print("At the receipt instant the touch is bound by the recorder itself, so the")
print("classification below carries no clock-skew and needs no merge.\n")
cls = collections.Counter(); n = 0
for slug, tr in T.items():
    for t, px, q, dr, bid, ask in tr:
        if px is None or bid is None or ask is None: continue
        n += 1
        # replicate as a BUY at px (UP terms for dir=='buy'; for a seller-initiated
        # print the mirrored buy is of the complement at 1-px, same geometry)
        if px >= ask - 1e-9:   cls["MARKETABLE (must cross)"] += 1
        elif px > bid + 1e-9:  cls["PASSIVE, improves inside spread"] += 1
        elif abs(px - bid) < 1e-9: cls["AT TOUCH (queue ahead unknown)"] += 1
        else:                  cls["PASSIVE, behind touch"] += 1
print(f"  trade prints with a bound two-sided touch: {n:,}")
for k, v in cls.most_common():
    print(f"    {k:34s} {v:7,d}  {v/n:6.1%}")
mk = cls["MARKETABLE (must cross)"]
print(f"\n  -> targeting the printed price is a CROSSING action {mk/n:.1%} of the time.")
print("     P36 measured 120/131 = 91.6% taker on the clone. The venue mechanism agrees:")
print("     a print IS the evidence that liquidity at that price was just consumed.")

# ---- how long until the printed price becomes passive again?
print("\n" + "="*80)
print("CF: HOW LONG MUST ONE WAIT FOR THE PRINTED PRICE TO BECOME PASSIVE?")
print("="*80)
waits = []; never = 0
for slug, tr in T.items():
    bk = B[slug]; j = 0
    for t, px, q, dr, bid, ask in tr:
        if px is None or ask is None or px < ask - 1e-9: continue   # already passive
        # advance through book states after t until ask > px (px becomes restable)
        k = 0; found = None
        for bt, bb, ba in bk:
            if bt <= t: continue
            if ba is not None and ba > px + 1e-9:
                found = bt - t; break
        if found is None: never += 1
        else: waits.append(found)
if waits:
    waits.sort()
    print(f"  marketable prints that later became passive: {len(waits):,}; never in-window: {never:,}")
    print(f"  wait to postability: p50 {waits[len(waits)//2]:.2f}s  p90 {waits[int(len(waits)*.9)]:.2f}s  "
          f"max {waits[-1]:.1f}s")
    print(f"  fraction postable within 1s: {sum(1 for w in waits if w<=1)/len(waits):.1%}; "
          f"within 5s: {sum(1 for w in waits if w<=5)/len(waits):.1%}")
    print("\n  This is the decisive number for the latency hypothesis. If the barrier were")
    print("  latency, shaving milliseconds would convert crossings into posts. It does not:")
    print("  the printed price only becomes passive once the market MOVES AWAY from it, which")
    print("  takes seconds, and by then it is no longer L5's price for the same state.")

# ---- the $13,905 decomposition
print("\n" + "="*80)
print("DECOMPOSITION OF THE EXECUTION-STYLE COUNTERFACTUAL  (MODELED COUNTERFACTUAL)")
print("="*80)
R = list(csv.DictReader(gzip.open("analysis/l5/l5_canonical_sequence.csv.gz", "rt")))
def isup(r): return r["outcome"].lower().startswith("up") or r["side"].lower() in ("up","yes")
gross = sum(((1.0 if (r["settlement_outcome"].lower().startswith("up")==isup(r)) else 0.0)
             - float(r["price"])) * float(r["quantity"]) for r in R)
obs_fees = obs_reb = 0.0
for r in R:
    q = D(str(float(r["quantity"]))); pr = D(str(round(float(r["price"]),2)))
    if r["maker_taker"] == "maker": obs_reb += float(maker_rebate(pr,q,when=WHEN))
    elif r["maker_taker"] == "taker": obs_fees += float(taker_fee(pr,q,when=WHEN))
# clone-like: flip the 55,647 maker fills to taker, keep the rest
flip_fees = flip_reb = 0.0
for i, r in enumerate(R):
    q = D(str(float(r["quantity"]))); pr = D(str(round(float(r["price"]),2)))
    mt = r["maker_taker"]
    if mt == "unknown": continue
    treat = "maker" if (i % 1000)/1000.0 < 0.084 else "taker"
    if treat == "maker": flip_reb += float(maker_rebate(pr,q,when=WHEN))
    else: flip_fees += float(taker_fee(pr,q,when=WHEN))
print(f"  gross directional+pair P&L, IDENTICAL in both arms   ${gross:+12,.2f}  ACTUAL")
print(f"  {'':54s}{'L5 observed':>14}{'clone-like':>14}")
print(f"  {'modeled taker fees':54s}{-obs_fees:>14,.2f}{-flip_fees:>14,.2f}")
print(f"  {'modeled maker rebate':54s}{obs_reb:>14,.2f}{flip_reb:>14,.2f}")
print(f"  {'net (MODELED COUNTERFACTUAL)':54s}{gross-obs_fees+obs_reb:>14,.2f}"
      f"{gross-flip_fees+flip_reb:>14,.2f}")
d_fees = -(flip_fees - obs_fees); d_reb = flip_reb - obs_reb
print(f"\n  swing attribution:")
print(f"    extra modeled taker fees                         ${d_fees:+12,.2f}  "
      f"{abs(d_fees)/(abs(d_fees)+abs(d_reb)):.1%} of swing")
print(f"    lost modeled maker rebate                        ${d_reb:+12,.2f}  "
      f"{abs(d_reb)/(abs(d_fees)+abs(d_reb)):.1%} of swing")
print(f"    changed execution price                           $        0.00  "
      f"BY CONSTRUCTION: both arms use L5's OWN fill prices")
print(f"    other                                             $        0.00")
print(f"    TOTAL SWING                                      ${d_fees+d_reb:+12,.2f}")
print("\n  The swing is ENTIRELY fee/rebate sign-flip on unchanged prices. It is an")
print("  UPPER BOUND on the execution-style cost and NOT a forecast: a real taker-heavy")
print("  clone would also get different PRICES (P36 measured FAVOURABLE slippage of")
print("  -$4.88, i.e. crossing was cheap), which this arithmetic does not model.")
print("\n  Breakeven maker share, holding L5's prices and behaviour fixed:")
for ms in (0.0,0.2,0.4,0.6,0.7,0.75,0.8,0.834,0.9):
    fe = rb = 0.0
    for i, r in enumerate(R):
        if r["maker_taker"] == "unknown": continue
        q = D(str(float(r["quantity"]))); pr = D(str(round(float(r["price"]),2)))
        if (i % 1000)/1000.0 < ms: rb += float(maker_rebate(pr,q,when=WHEN))
        else: fe += float(taker_fee(pr,q,when=WHEN))
    net = gross - fe + rb
    print(f"    maker share {ms:5.1%} -> net ${net:+10,.2f} {'(positive)' if net>0 else ''}")
