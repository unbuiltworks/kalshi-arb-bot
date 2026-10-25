"""Is the market's own price already right, and when?

THE QUESTION THIS SETTLES. The closing-average mechanism makes a window
progressively more decided as it runs down -- 0% decided with the whole closing
minute ahead, ~100% decided with 10 s left (see brti_determinacy.py). An
estimator built on that mechanism can therefore only know things LATE.

That is only worth money if the MARKET does not also know them. So this
measures the market's own Brier score against realized settlement, bucketed by
time to close, on venue-native settlement labels only. If the mid is already
sharp and calibrated late in the window, the mechanism yields no edge no matter
how well it is modelled -- it would be predicting what everyone can see.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))

BUCKETS = [(0, 15), (15, 30), (30, 60), (60, 120), (120, 300), (300, 900)]


def load(path, label_filter=None):
    ws = json.load(open(path))
    if label_filter:
        ws = [w for w in ws if w["settle_src"] == label_filter]
    return ws


def mid_of(frame):
    _, yb, _, nb, _ = frame[0], D(frame[1]), D(frame[2]), D(frame[3]), D(frame[4])
    return float((yb + (D(1) - nb)) / D(2))


def analyse(ws, name):
    rows = {b: [] for b in BUCKETS}
    for w in ws:
        close, won = w["close"], w["yes_won"]
        for fr in w["bbo"]:
            t = fr[0]
            tte = close - t
            if tte < 0:
                continue
            for b in BUCKETS:
                if b[0] <= tte < b[1]:
                    rows[b].append((mid_of(fr), won))
                    break
    print(f"\n{name}: {len(ws)} windows, venue-native labels")
    hdr = (f"{'tte bucket':>14s} {'n':>6s} {'Brier(mid)':>11s} {'Brier(0.5)':>11s} "
           f"{'skill vs coin':>13s} {'mean|mid-.5|':>13s} {'extreme%':>9s}")
    print(hdr); print("-" * len(hdr))
    out = {}
    for b in BUCKETS:
        s = rows[b]
        if len(s) < 10:
            continue
        bm = statistics.mean([(p - y) ** 2 for p, y in s])
        b5 = 0.25
        dist = statistics.mean([abs(p - 0.5) for p, _ in s])
        ext = 100.0 * sum(1 for p, _ in s if p <= 0.05 or p >= 0.95) / len(s)
        out[f"{b[0]}-{b[1]}s"] = dict(n=len(s), brier=bm,
                                      skill_vs_coin=(b5 - bm) / b5,
                                      mean_abs_dev=dist, extreme_pct=ext)
        print(f"{b[0]:>6d}-{b[1]:<7d} {len(s):6d} {bm:11.5f} {b5:11.5f} "
              f"{100*(b5-bm)/b5:12.1f}% {dist:13.3f} {ext:8.1f}%")
    return out


def main():
    res = {}
    clean = os.path.join(HERE, "windows_clean_labels.json")
    if os.path.exists(clean):
        res["kalshi_terminal_print"] = analyse(load(clean), "KALSHI (terminal_print only)")
    pmus = os.path.join(HERE, "pmus_windows.json")
    if os.path.exists(pmus):
        res["pmus"] = analyse(load(pmus), "PMUS (terminal_print_expired)")

    print("\nREADING THIS TABLE")
    print("  'skill vs coin' is how much better the market's mid is than 0.50.")
    print("  A mid that is already near 0 or 1 late in the window, with a low")
    print("  Brier, means the market has ALREADY priced what the closing-average")
    print("  mechanism reveals. An estimator of that mechanism then has nothing")
    print("  left to sell, however good it is.")
    out = os.path.join(HERE, "inventory", "market_calibration_by_tte.json")
    json.dump(res, open(out, "w"), indent=1)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
def executable_edge(ws, name):
    """Even a PERFECT forecaster has to cross a spread and pay a fee.

    This is the number that decides the whole question: the average absolute
    mispricing of the market's own mid, bucketed by time to close, set against
    the cost of acting on it. A perfect forecast of settlement is worth
    |outcome - mid| per contract GROSS. If that is smaller than half the spread
    plus the fee, it cannot be traded at any size, and no improvement to the
    estimator changes that.
    """
    from src.gemini_execution.pmus_execution_model import CalibratedFillModel
    model = CalibratedFillModel.load()
    rows = {b: [] for b in BUCKETS}
    for w in ws:
        close, won = w["close"], w["yes_won"]
        for fr in w["bbo"]:
            t = fr[0]
            tte = close - t
            if tte < 0:
                continue
            yb, nb = D(fr[1]), D(fr[3])
            spread = float(D(1) - yb - nb)
            mid = mid_of(fr)
            for b in BUCKETS:
                if b[0] <= tte < b[1]:
                    rows[b].append((mid, won, spread))
                    break
    print(f"\n{name}: EXECUTABLE edge of a PERFECT forecaster")
    hdr = (f"{'tte bucket':>14s} {'n':>6s} {'gross/ctr':>10s} {'spread':>8s} "
           f"{'taker fee':>10s} {'net taker':>10s} {'net maker':>10s}")
    print(hdr); print("-" * len(hdr))
    out = {}
    for b in BUCKETS:
        s = rows[b]
        if len(s) < 10:
            continue
        gross = statistics.mean([abs(y - p) for p, y, _ in s])
        spr = statistics.mean([sp for _, _, sp in s])
        # a taker crossing to the side it wants pays half the spread and the fee
        fee = float(model.taker_fee(D(1), D("0.5")))
        net_taker = gross - spr / 2.0 - fee
        # a maker pays no fee but only fills with probability p_fill, and only
        # captures the edge when filled
        pf = model.p_fill(leg="first_leg", rest_s=30.0) or 0.0
        net_maker = gross * pf
        out[f"{b[0]}-{b[1]}s"] = dict(n=len(s), gross=gross, spread=spr,
                                      net_taker=net_taker, net_maker=net_maker)
        print(f"{b[0]:>6d}-{b[1]:<7d} {len(s):6d} {gross:10.5f} {spr:8.4f} "
              f"{fee:10.4f} {net_taker:10.5f} {net_maker:10.5f}")
    return out
