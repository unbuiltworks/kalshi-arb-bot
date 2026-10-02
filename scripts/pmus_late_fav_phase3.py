#!/usr/bin/env python3
"""Cap economics sensitivity + Phase 3 forward-sample requirement. DISCOVERY."""
import json, math, sys, statistics as st
sys.path.insert(0, ".")
from src.pmus_fees import taker_fee, maker_rebate
from decimal import Decimal as D
import datetime as dt
WHEN = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)

P = 0.99
fee = float(taker_fee(D("0.99"), D(1), when=WHEN))
reb = float(maker_rebate(D("0.99"), D(1), when=WHEN))
print("=== ECONOMICS AT THE 0.99 PRICE CAP (where 55% of buyable late states sit) ===")
print(f"  buy 1 share of the favorite at {P}: win -> +{(1-P)*100:.2f}c, lose -> -{P*100:.2f}c")
print(f"  taker fee {fee*100:.4f}c/share, maker rebate {reb*100:.4f}c/share\n")
print(f"  {'true p':>8} {'taker EV/sh':>12} {'maker EV/sh':>12} {'sh for $3':>11} {'sh for $5':>11}")
for p in (0.9999, 0.999, 0.998, 0.996, 0.995, 0.993, 0.9907, 0.99, 0.985, 0.98):
    et = p * (1 - P) - (1 - p) * P - fee
    em = p * (1 - P) - (1 - p) * P + reb
    s3 = f"{3/et:,.0f}" if et > 0 else "impossible"
    s5 = f"{5/et:,.0f}" if et > 0 else "impossible"
    print(f"  {p:8.4f} {et*100:+11.4f}c {em*100:+11.4f}c {s3:>11} {s5:>11}")
print(f"\n  TAKER BREAKEVEN true hold rate = {P+fee:.4%}")
print(f"  MAKER BREAKEVEN true hold rate = {P-reb:.4%}  (the rebate buys {reb*100:.3f}c of room)")
print("  The entire question is whether the true tail probability sits above ~99.07%.")
print("  That is a third/fourth-significant-figure question about a rare event.")

S = json.load(open("analysis/pmus_late_favorite/states_joined.json"))["states"]
cap = [s for s in S if s["fav_ask"] is not None and s["fav_ask"] >= 0.985]
d = [s["ask_qty"] if s["fav_side"] == "UP" else s["bid_qty"] for s in cap]
d = [x for x in d if x]
if d:
    d.sort()
    print(f"\n  observed displayed depth at the touch in cap states: n={len(d)} "
          f"min={d[0]:.0f} p25={d[len(d)//4]:.0f} p50={d[len(d)//2]:.0f} max={d[-1]:.0f} shares")
    print("  -> at p=0.995 you need ~700 shares/window for $3. That is inside the p50 displayed")
    print("     depth but OUTSIDE it in the thinnest windows. Capacity is window-dependent, and")
    print("     this is displayed depth only -- not a filled, cleared or settled quantity.")

print("\n=== PHASE 3: FORWARD SAMPLE REQUIRED FOR A CREDIBLE OOS VERDICT ===")
def n_needed(p1, p0, alpha=1.645, beta=0.84):
    pb = (p1 + p0) / 2
    return (alpha + beta) ** 2 * pb * (1 - pb) / (p1 - p0) ** 2
print("  Test: is the true hold rate in the >=2.0 |z| / cap state ABOVE taker breakeven?")
print(f"  {'H1 true p':>10} {'vs breakeven':>13} {'independent markets needed':>28}")
for p1 in (0.9995, 0.999, 0.998, 0.996, 0.995, 0.994, 0.993):
    n = n_needed(p1, P + fee)
    print(f"  {p1:10.4f} {P+fee:13.4f} {n:28,.0f}")
print("\n  One-sided, alpha=0.05, power=0.80, markets as the independent unit (NOT ticks,")
print("  NOT checkpoints -- the 5 checkpoints of one market are the same market).")

per_day = 96
traded = 0.393        # P24: 35/89 closed windows carried any volume
reach = 0.55          # share of buyable late states observed at the cap
eff = per_day * traded * reach
print(f"\n  Accrual arithmetic: {per_day} windows/day x {traded:.1%} that trade at all "
      f"x {reach:.0%} reaching a cap state ~= {eff:.0f} eligible markets/day")
for p1, lab in ((0.995, "p=0.995 (modest edge)"), (0.998, "p=0.998 (strong edge)")):
    n = n_needed(p1, P + fee)
    print(f"    {lab}: {n:,.0f} markets ~= {n/eff:,.0f} CALENDAR DAYS of clean capture")
print("\n  Minimum requirements for those markets to count:")
print("   * full L2 ladder + authenticated trade tape + reference, all synchronized, per window")
print("     (the CURRENT post-ddc2195c recorder emits full L2 at ~2s, so this is now feasible)")
print("   * sub-minute BTC reference -- btc-ref-collector already runs and is the right source;")
print("     a 1-minute proxy cannot measure distance at T-60/T-30 (staleness up to 60s)")
print("   * SETTLEMENT FROM BRTI or the venue's own resolution record, not an OHLC4 proxy")
print("   * >=20 distinct days spanning calm and volatile regimes (day and data-source are")
print("     CONFOUNDED in the current discovery sample: auth=09-30 only, public=09-27/28 only)")
print("   * >=1 market per cell is not enough: target >=150 markets per (checkpoint x |z|) cell")
print("     for the 25-cell grid, i.e. >=3,750 market-observations of grid coverage")
print("   * adverse selection needs LOSSES in the sample: at a 0.5% loss rate you need ~600")
print("     defensible maker fills to observe ~3 losing fills, and ~2,000+ fills to estimate")
print("     P(correct|fill) to +/-0.3%. At the observed 30% fill rate that is >=2,000 placements.")
print("   * ZERO use of the active experiment's reserved windows to reach any of it.")
