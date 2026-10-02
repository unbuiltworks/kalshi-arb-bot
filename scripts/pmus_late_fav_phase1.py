#!/usr/bin/env python3
"""Phase 1 calibration report. DISCOVERY ONLY. No trading-rule P&L."""
from __future__ import annotations
import json, collections, math, statistics as st, sys
sys.path.insert(0, ".")
from src.pmus_fees import taker_fee
from decimal import Decimal as D
import datetime as dt

S = json.load(open("analysis/pmus_late_favorite/states_joined.json"))["states"]
CP = [300, 180, 120, 60, 30]
ZB = [(0, .5), (.5, 1.), (1., 1.5), (1.5, 2.), (2., 9e9)]
ZL = ["<0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", ">=2.0"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0, c - h), min(1, c + h))


def clustered_se(rows):
    """Cluster residuals by market: each market contributes its mean residual."""
    g = collections.defaultdict(list)
    for r in rows:
        g[r["slug"]].append(r["favorite_won"] - r["fav_ask"])
    m = [st.mean(v) for v in g.values()]
    if len(m) < 2:
        return None, len(m)
    return st.stdev(m) / math.sqrt(len(m)), len(m)


U = [s for s in S if s["fav_ask"] is not None and s["favorite_won"] is not None]
print(f"PHASE 1 -- DISCOVERY ONLY. usable states {len(U)} over {len({s['slug'] for s in U})} markets")
print(f"(of {len(S)} built states; {len(S)-len(U)} have no executable favorite price -- see UNBUYABLE below)\n")

print("=== GRID: time remaining x |z| bucket ===")
print(f"{'T-':>5} {'|z|':>8} {'mkts':>4} {'obs':>4} {'favwin':>7} {'rate':>6} "
      f"{'fav_ask':>7} {'resid':>7} {'clus_se':>7} {'wilson95':>14} {'brier_mkt':>9} {'brier_px':>8}")
cells = {}
for cp in CP:
    for (lo, hi), lab in zip(ZB, ZL):
        g = [s for s in U if s["checkpoint_s_remaining"] == cp and lo <= s["abs_z"] < hi]
        if not g:
            continue
        n = len(g); k = sum(s["favorite_won"] for s in g)
        mk = len({s["slug"] for s in g})
        fa = st.mean(s["fav_ask"] for s in g)
        res = k / n - fa
        se, nc = clustered_se(g)
        w = wilson(k, mk)   # interval on MARKETS, not observations
        bp = st.mean((s["fav_ask"] - s["favorite_won"]) ** 2 for s in g)
        bm = st.mean((k / n - s["favorite_won"]) ** 2 for s in g)
        cells[(cp, lab)] = g
        print(f"{cp:5d} {lab:>8} {mk:4d} {n:4d} {k:3d}/{n:<3d} {k/n:6.2f} "
              f"{fa:7.3f} {res:+7.3f} {('%.3f'%se) if se else '    n/a':>7} "
              f"{('[%.2f,%.2f]'%w) if w[0] is not None else 'n/a':>14} {bm:9.3f} {bp:8.3f}")

print("\n=== COLLAPSED: |z| bucket pooled over checkpoints (markets clustered) ===")
for (lo, hi), lab in zip(ZB, ZL):
    g = [s for s in U if lo <= s["abs_z"] < hi]
    if not g:
        continue
    n = len(g); k = sum(s["favorite_won"] for s in g); mk = len({s["slug"] for s in g})
    fa = st.mean(s["fav_ask"] for s in g); se, nc = clustered_se(g)
    res = k / n - fa
    tval = (res / se) if se else None
    print(f"  |z| {lab:>8}  mkts={mk:2d} obs={n:3d}  rate={k/n:.3f}  fav_ask={fa:.3f}  "
          f"resid={res:+.3f}  clustered_se={('%.3f'%se) if se else 'n/a'}  "
          f"t={('%.2f'%tval) if tval else 'n/a'}")

print("\n=== INDIVIDUAL MARKETS BEHIND EVERY CELL WITH |resid| > 0.05 ===")
for (cp, lab), g in sorted(cells.items()):
    n = len(g); k = sum(s["favorite_won"] for s in g)
    res = k / n - st.mean(s["fav_ask"] for s in g)
    if abs(res) <= 0.05:
        continue
    print(f"\n  T-{cp} |z|{lab}  resid={res:+.3f} from {len(g)} obs / {len({s['slug'] for s in g})} markets:")
    for s in sorted(g, key=lambda x: x["slug"]):
        print(f"    {s['slug'][19:]}  z={s['z']:+6.2f} bps={s['dist_bps_signed']:+7.2f} "
              f"fav={s['fav_side']:4s} ask={s['fav_ask']:.3f} won={s['favorite_won']} "
              f"src={'AUTH' if s['source']=='authenticated_ws' else 'pub '} day={s['day']}")

print("\n=== THE 0.99 PRICE CAP -- what it does to any high-|z| edge ===")
cap = [s for s in U if s["fav_ask"] >= 0.985]
print(f"  states with fav_ask >= 0.985: {len(cap)} / {len(U)} ({len(cap)/len(U):.0%}), "
      f"over {len({s['slug'] for s in cap})} markets")
print(f"  their favorite win rate: {sum(s['favorite_won'] for s in cap)}/{len(cap)}")
print(f"  mean fav_ask: {st.mean(s['fav_ask'] for s in cap):.4f}  (venue max price is 0.99)")
w = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
for p in ("0.99", "0.98"):
    f = float(taker_fee(D(p), D(1), when=w))
    be = (float(p) + f)
    print(f"  buying favorite at {p}: taker fee {f*100:.3f}c/share; "
          f"gross max gain {(1-float(p))*100:.1f}c; BREAKEVEN true hold rate = {be:.4%}")
print("  -> the maximum gross edge at the cap is 1 cent, and breakeven is a 4th-significant-figure")
print("     question about a tail probability. 6 observations cannot resolve it.")

print("\n=== UNBUYABLE FAVORITE (no executable price) ===")
nb = [s for s in S if s["fav_ask"] is None]
print(f"  {len(nb)}/{len(S)} states ({len(nb)/len(S):.0%}) have NO executable favorite price.")
print(f"  all one_sided: {all(s['one_sided'] for s in nb)}; all fav=DOWN: {all(s['fav_side']=='DOWN' for s in nb)}")
print("  mechanism: book_bid_up is None, so 'buy DOWN at 1-bid' does not exist -- nobody is")
print("  offering the favorite at any price. Expression A is simply unavailable in these states.")
print(f"  by checkpoint: {dict(collections.Counter(s['checkpoint_s_remaining'] for s in nb))}")
print(f"  mean |z| where unbuyable: {st.mean(s['abs_z'] for s in nb):.2f} "
      f"vs buyable {st.mean(s['abs_z'] for s in U):.2f}")

print("\n=== RAW-BPS DIAGNOSTIC (not a strategy bucket) ===")
for lo, hi in [(0, 2), (2, 5), (5, 10), (10, 15), (15, 25), (25, 40), (40, 9e9)]:
    g = [s for s in U if lo <= s["dist_bps_abs"] < hi]
    if not g:
        continue
    k = sum(s["favorite_won"] for s in g)
    print(f"  |bps| {lo:>2}-{hi if hi<9e8 else '+':>3}: mkts={len({s['slug'] for s in g}):2d} obs={len(g):3d} "
          f"rate={k/len(g):.2f} fav_ask={st.mean(s['fav_ask'] for s in g):.3f} "
          f"resid={k/len(g)-st.mean(s['fav_ask'] for s in g):+.3f}")

print("\n=== CONCENTRATION BY DAY / REGIME ===")
for d in sorted({s["day"] for s in U}):
    g = [s for s in U if s["day"] == d]
    k = sum(s["favorite_won"] for s in g)
    print(f"  {d}: mkts={len({s['slug'] for s in g}):2d} obs={len(g):3d} rate={k/len(g):.2f} "
          f"fav_ask={st.mean(s['fav_ask'] for s in g):.3f} resid={k/len(g)-st.mean(s['fav_ask'] for s in g):+.3f} "
          f"src={sorted({s['source'][:4] for s in g})}")
print("  NOTE: the authenticated markets are ALL on 2026-09-30 within one 90-minute stretch;")
print("        the public markets are ALL on 2026-09-27/28. Day and data-source are CONFOUNDED.")
