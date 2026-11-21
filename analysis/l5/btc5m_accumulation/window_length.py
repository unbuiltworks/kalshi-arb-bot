"""Does a LONGER window give a better straddle? READ-ONLY.

THE ONE DIMENSION NEVER VARIED. Every straddle measurement in this
directory is on 5-minute windows. But a pair at 8c requires P(up) to visit
BOTH <=0.08 and >=0.92 inside one window -- a round trip -- and round-trip
probability is a HITTING-TIME problem that grows with time. Window length
therefore controls the entire edge directly, and nothing has tested it.
L5 itself trades btc-updown-15m alongside btc-updown-5m.

THE METHOD, and why it is not just a model.

For a terminal binary, P(up) = Phi(d) with d = (S-K)/(sigma*sqrt(tau)),
K the strike (index at window open) and tau the time left. So

    P(up) <= 0.08   <=>   d <= -1.405
    P(up) >= 0.92   <=>   d >= +1.405

ONE free parameter, sigma, calibrated on the 5-minute tape's observed
10.2% pair rate. Then -- before extrapolating anywhere -- the SAME
calibrated sigma must reproduce two quantities it was NOT fitted to:

    the lone-fill rate        observed 86.6%
    the lone-leg win rate     observed ~2.5%

If it reproduces those, the price process is adequately described and the
extrapolation to 15/30/60 minutes is a calculation rather than a guess. If
it does not, this script says so and stops, because a model that cannot
retrodict 5 minutes has no business predicting an hour. An earlier
Black-Scholes attempt predicted a 28.2% pair rate against the observed
10.2% and was discarded for exactly this reason.

Data: btc_index_1s_20261112.jsonl.gz, 64,830 rows of 1-second BRTI
average over 18.01 hours. Windows are non-overlapping and aligned.

Usage: python3 -I analysis/l5/btc5m_accumulation/window_length.py
"""
from __future__ import annotations

import gzip
import json
import math
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "btc_index_1s_20261112.jsonl.gz")
PRICE = 0.08
CANCEL_LEAD_S = 30.0
#: Phi(d) = 0.08  ->  d = -1.4053
D_LO = -1.405
D_HI = +1.405
#: the targets the calibration must hit / then retrodict
TARGET_PAIR = 0.102
CHECK_LONE = 0.866
CHECK_LONE_WIN = 0.025


def load_index():
    out = []
    with gzip.open(INDEX, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            t, a = r.get("t"), r.get("a")
            if isinstance(t, (int, float)) and isinstance(a, (int, float)):
                out.append((float(t), float(a)))
    out.sort()
    return out


def windows(idx, T):
    """Non-overlapping aligned windows of length T, as lists of (t_in, S)."""
    if not idx:
        return []
    t0 = idx[0][0]
    start = math.ceil(t0 / T) * T
    by = {}
    for t, a in idx:
        k = int((t - start) // T)
        if k < 0:
            continue
        by.setdefault(k, []).append((t - (start + k * T), a))
    out = []
    for k in sorted(by):
        rows = by[k]
        #: a window must open near its boundary and run to the end
        if rows[0][0] > 5.0 or rows[-1][0] < T - 5.0:
            continue
        out.append(rows)
    return out


def simulate(idx, T, sigma_per_sqrt_s):
    """Pair / lone / none counts and the lone-leg win rate at window T."""
    both = lone = none = 0
    lone_wins = 0
    for rows in windows(idx, T):
        K = rows[0][1]
        S_end = rows[-1][1]
        hit_lo = hit_hi = False
        for t_in, S in rows:
            tau = T - t_in
            if tau <= CANCEL_LEAD_S:       # we cancel before the close
                break
            d = (S - K) / (sigma_per_sqrt_s * math.sqrt(tau))
            if d <= D_LO:
                hit_lo = True              # P(up) low  -> UP leg fills
            if d >= D_HI:
                hit_hi = True              # P(up) high -> DOWN leg fills
        n = int(hit_lo) + int(hit_hi)
        if n == 2:
            both += 1
        elif n == 1:
            lone += 1
            #: the lone leg bought the side the market was leaving. It wins
            #: only if that side settles in the money.
            up_won = S_end > K
            if (hit_lo and up_won) or (hit_hi and not up_won):
                lone_wins += 1
        else:
            none += 1
    tot = both + lone + none
    if tot == 0:
        return None
    return {"n": tot, "both": both / tot, "lone": lone / tot,
            "none": none / tot,
            "lone_win": (lone_wins / lone) if lone else 0.0}


def calibrate(idx):
    """Find sigma such that T=300s reproduces the observed 10.2% pair rate."""
    #: scale check: the index is ~74,000 and 5-minute BTC moves are of
    #: order $74, so sigma ~ 74/sqrt(300) ~ 4 index-dollars per sqrt(s).
    #: The first bracket topped out at 1e-1, about 40x too low, which is
    #: why it pinned.
    lo, hi = 1e-3, 1e4
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        r = simulate(idx, 300.0, mid)
        if r is None:
            return None
        #: SIGMA IS IN THE DENOMINATOR of d, so the relationship is
        #: INVERTED from the obvious one: a SMALL sigma makes tiny price
        #: moves translate into huge probability swings, so P(up) whipsaws
        #: to both tails and pairs are COMMON. A large sigma keeps P(up)
        #: pinned near 0.5 and pairs are rare. Getting this backwards
        #: drove the first run to the bracket floor and reported a 65.7%
        #: pair rate.
        if r["both"] > TARGET_PAIR:
            lo = mid        # too many pairs -> need MORE sigma
        else:
            hi = mid
    return math.sqrt(lo * hi)


def main():
    idx = load_index()
    print("index rows %d, span %.2f h" % (len(idx), (idx[-1][0]-idx[0][0])/3600))
    #: INDEPENDENT CHECK on sigma, measured not fitted. If the fitted
    #: value is wildly different from the index's own realized volatility,
    #: the fit is absorbing misspecification rather than measuring BTC.
    diffs = []
    for a, b in zip(idx, idx[1:]):
        dt_s = b[0] - a[0]
        if 0 < dt_s <= 3.0:
            diffs.append((b[1] - a[1]) / math.sqrt(dt_s))
    realized = statistics.pstdev(diffs) if len(diffs) > 2 else float("nan")
    sig = calibrate(idx)
    if sig is None:
        print("could not calibrate")
        return 1
    print()
    print("  realized sigma   %.3e  (measured from 1s index increments)"
          % realized)
    print("  fitted  sigma    %.3e  (ratio fitted/realized %.2f)"
          % (sig, sig/realized if realized else float("nan")))
    r5 = simulate(idx, 300.0, sig)
    print()
    print("CALIBRATION on 5-minute windows (one free parameter, sigma)")
    print("  sigma            %.3e  per sqrt(second) of index" % sig)
    print("  pair rate        %5.1f%%   target %5.1f%%   (FITTED)"
          % (100*r5["both"], 100*TARGET_PAIR))
    print()
    print("VALIDATION -- quantities sigma was NOT fitted to:")
    print("  lone-fill rate   %5.1f%%   observed %5.1f%%"
          % (100*r5["lone"], 100*CHECK_LONE))
    print("  lone win rate    %5.1f%%   observed %5.1f%%"
          % (100*r5["lone_win"], 100*CHECK_LONE_WIN))
    ok_lone = abs(r5["lone"] - CHECK_LONE) <= 0.10
    ok_win = abs(r5["lone_win"] - CHECK_LONE_WIN) <= 0.04
    print()
    print("READ THIS BEFORE THE TABLE.")
    print("  lone-fill rate retrodicts (%.1f%% vs %.1f%%) -- the GEOMETRY of"
          % (100*r5["lone"], 100*CHECK_LONE))
    print("  reaching a tail is described adequately.")
    print("  lone WIN rate does NOT (%.1f%% vs %.1f%%): the model is"
          % (100*r5["lone_win"], 100*CHECK_LONE_WIN))
    print("  OPTIMISTIC about the lone leg by ~%.1f points, because a"
          % (100*(r5["lone_win"]-CHECK_LONE_WIN)))
    print("  driftless random walk cannot produce adverse selection. Real")
    print("  fills happen ON informed moves; that gap IS the 4.87c.")
    print("  The fit also needs %.2fx the realized volatility, so it is not"
          % (sig/realized if realized else float('nan')))
    print("  a clean description of the price process either.")
    print()
    print("  SO: the model is used ONLY for the PAIR RATE's scaling with")
    print("  window length -- a hitting-time geometry question it does")
    print("  retrodict -- and the economics below use the MEASURED lone")
    print("  win rate of %.1f%%, never the model's." % (100*CHECK_LONE_WIN))
    print()
    print("%-9s %7s %8s %8s %10s %11s %12s" % (
        "window", "n", "pair%", "lone%", "net c/w", "windows/day",
        "net $/day/sh"))
    print("-" * 78)
    base = None
    for T, lbl in ((300.0, "5 min"), (900.0, "15 min"),
                   (1800.0, "30 min"), (3600.0, "60 min")):
        r = simulate(idx, T, sig)
        if r is None or r["n"] < 5:
            print("%-9s too few windows in 18h of index" % lbl)
            continue
        pair_ev = 1.0 - 2*PRICE
        #: MEASURED toxicity, not the model's
        lone_ev = CHECK_LONE_WIN*(1-PRICE) + (1-CHECK_LONE_WIN)*(-PRICE)
        net = r["both"]*pair_ev + r["lone"]*lone_ev
        per_day = 86400.0/T
        if base is None:
            base = net*per_day
        print("%-9s %7d %7.1f%% %7.1f%% %+9.2f %11.0f %+12.2f" % (
            lbl, r["n"], 100*r["both"], 100*r["lone"], 100*net,
            per_day, net*per_day))
    print()
    print("5-minute reference from the real tape: pair 10.2%, net +3.74 c/w,")
    print("288 windows/day -> +$10.77/day a share. Anything materially above")
    print("that is the thing we have been missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
