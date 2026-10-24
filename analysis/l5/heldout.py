"""Held-out stranded-leg recovery evaluation.

PATH + OUTCOME come from L5's Polymarket window corpus (the same BTC/ETH
up-down instrument family). FEES are PMUS's own curve. Polymarket fill
economics are NOT transferred: the cross action is priced at our venue's
taker fee and charged a full 1-tick spread penalty.
"""
import csv, collections, statistics as st
from decimal import Decimal as D

BASE = "/home/user/kalshi-arb-bot/analysis/l5"
TAKER_THETA = D("0.0695")
SPREAD_PENALTY = D("0.01")          # exit at the bid, 1 tick worse than the print
def fee(p): return TAKER_THETA * p * (D(1) - p)

def load(minutes):
    sums = {r["window_id"]: r for r in csv.DictReader(open(f"{BASE}/l5_windows_summary.csv"))
            if r["window_minutes"] == minutes and r["winning_outcome"] in ("Up", "Down")
            and r["residual_side"] in ("Up", "Down")
            and float(r["residual_qty_abs"] or 0) > 0 and float(r["residual_cost"] or 0) > 0}
    paths = collections.defaultdict(list)
    for r in csv.DictReader(open(f"{BASE}/l5_window_sequences.csv")):
        w = r["window_id"]
        if w not in sums: continue
        try:
            t = float(r["seconds_from_window_start"]); p = D(r["price"])
        except Exception: continue
        up = p if r["outcome"] == "Up" else D(1) - p
        if not (D("0.005") <= up <= D("0.995")): continue
        paths[w].append((t, up))
    for w in paths: paths[w].sort()
    return sums, paths

def up_at(path, t):
    """Last traded UP price at or before t; None if the window has no print yet."""
    v = None
    for tt, up in path:
        if tt <= t: v = up
        else: break
    return v

def evaluate(minutes, decision_ts):
    sums, paths = load(minutes)
    usable = [w for w in sums if len(paths.get(w, [])) >= 5]
    print(f"\n{'='*86}\n{minutes}-MINUTE WINDOWS  "
          f"({len(usable)} with a residual, a resolution and >=5 prints)\n{'='*86}")
    # calibration first: does our side's price at t predict it winning at that rate?
    print(f"\nBOOK CALIBRATION of the STRANDED side (is the price at T+t honest?)")
    print(f"  {'T+':>5} {'n':>5} {'mean price':>11} {'realized win':>13} {'gap':>8}")
    for t in decision_ts:
        xs = []
        for w in usable:
            s = sums[w]; up = up_at(paths[w], t)
            if up is None: continue
            ours = up if s["residual_side"] == "Up" else D(1) - up
            won = 1 if s["residual_side"] == s["winning_outcome"] else 0
            xs.append((float(ours), won))
        if len(xs) < 30: continue
        mp = st.mean(x[0] for x in xs); wr = st.mean(x[1] for x in xs)
        print(f"  {t:>5} {len(xs):>5} {mp:>11.4f} {wr:>13.4f} {wr-mp:>+8.4f}")
    # then the policy comparison, per unit of residual
    print(f"\nREALIZED RECOVERY ECONOMICS per unit of stranded inventory")
    print(f"  {'T+':>5} {'n':>5} {'HOLD':>9} {'CROSS':>9} {'CROSS-HOLD':>11} "
          f"{'HOLD p05':>9} {'CROSS p05':>9} {'HOLD worst':>11}")
    best = None
    for t in decision_ts:
        hold, cross = [], []
        for w in usable:
            s = sums[w]; up = up_at(paths[w], t)
            if up is None: continue
            q = D(s["residual_qty_abs"]); c = D(s["residual_cost"]) / q
            if not (D("0.01") <= c <= D("0.99")): continue
            ours = up if s["residual_side"] == "Up" else D(1) - up
            exit_px = max(D("0.01"), ours - SPREAD_PENALTY)
            won = 1 if s["residual_side"] == s["winning_outcome"] else 0
            hold.append(float(D(won) - c))
            cross.append(float(exit_px - c - fee(D(1) - exit_px)))
        if len(hold) < 30: continue
        mh, mc = st.mean(hold), st.mean(cross)
        hs, cs = sorted(hold), sorted(cross)
        p5 = lambda a: a[max(0, round(0.05*(len(a)-1)))]
        if best is None or mc - mh > best[1]: best = (t, mc - mh, mc, mh, len(hold))
        print(f"  {t:>5} {len(hold):>5} {mh:>+9.4f} {mc:>+9.4f} {mc-mh:>+11.4f} "
              f"{p5(hs):>+9.4f} {p5(cs):>+9.4f} {min(hs):>+11.4f}")
    if best:
        print(f"\n  best decision time T+{best[0]}: CROSS {best[2]:+.4f} vs HOLD {best[3]:+.4f} "
              f"= {best[1]:+.4f}/unit over n={best[4]}")
    return usable, sums, paths

evaluate("15", [120, 180, 240, 300, 360, 420, 480, 540, 600, 660, 720, 780])
evaluate("5",  [30, 60, 90, 120, 150, 180, 210, 240, 270])
