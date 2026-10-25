"""How decided is a PMUS BTC15m window with tau seconds left, really?

THE CLAIM UNDER TEST: "approximately 83% of settlement value is fixed at 10
seconds remaining." It is arithmetically true and strategically almost empty,
and this script separates the two.

  * 83.3% is (60-10)/60 -- the WEIGHT of the closing average's observations
    that have already happened. That is exact and not in dispute.
  * Whether the OUTCOME is decided depends on the distance between the partial
    average and the strike, measured in units of how far the remaining
    observations can move it. That is what is computed here.

THE SERIES USED IS A PROXY AND CANNOT SETTLE A WINDOW. We hold zero BRTI
observations (`src/btc_ref_recorder.py` records Coinbase and says so itself).
What a proxy legitimately supports is the STRUCTURE of the statistic -- how
determined a 60-point average is with tau left -- because that follows from
averaging and from volatility, not from which index is averaged. Every number
here is therefore labelled PROXY, and none of it is a settlement replay or a
reported return.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from src.gemini_execution.brti_settlement import (
    N_OBS, PartialState, SOURCE_PROXY, determined_fraction,
)

TICKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tick", "spot.json")


def one_hz(path):
    """Collapse the tick stream to one observation per second: the last trade
    price in each second. That is the shape a 60-per-minute index has, and the
    only shape in which the 60-observation average is meaningful."""
    rows = json.load(open(path))
    per = {}
    for r in rows:
        px = r.get("px")
        ev = r.get("ev_ns")
        if px is None or ev is None:
            continue
        per[int(ev) // 1_000_000_000] = float(px)
    return sorted(per.items())


def main():
    series = one_hz(TICKS)
    print(f"PROXY series: {len(series)} one-second observations "
          f"(Coinbase, NOT BRTI, cannot settle a window)")
    if len(series) < 3 * N_OBS:
        print("too short")
        return 1
    t0, t1 = series[0][0], series[-1][0]
    print(f"span {t1 - t0} s, contiguity "
          f"{100.0*len(series)/max(1,(t1-t0+1)):.1f}% of seconds present")

    vals = [v for _, v in series]
    steps = [b - a for a, b in zip(vals, vals[1:])]
    sigma = statistics.stdev(steps)
    print(f"\nper-second step sd (PROXY): ${sigma:.2f}   "
          f"median |step| ${statistics.median([abs(s) for s in steps]):.2f}")

    # ---- the weight claim, exactly
    print("\nTHE 83% CLAIM -- weight of already-elapsed observations (exact):")
    for tau in (60, 30, 10, 5, 1):
        print(f"  tau={tau:2d}s  weight fixed {determined_fraction(tau):.4f}")
    print("  tau=10 -> 0.8333. The claim is ARITHMETICALLY CORRECT.")

    # ---- how much the remaining observations can still move the average
    print("\nBUT: how much can the remaining tau observations still move the "
          "closing average?")
    print("  sd of the closing average's remaining contribution, (tau/60)*M_tau,")
    print("  under a random walk (NOT independence, which would understate it):")
    for tau in (30, 10, 5, 1):
        t = float(tau)
        var_mean = sigma ** 2 * (t + 1.0) * (2.0 * t + 1.0) / (6.0 * t)
        sd_contrib = (t / N_OBS) * math.sqrt(var_mean)
        sd_indep = (t / N_OBS) * (sigma / math.sqrt(t))
        print(f"  tau={tau:2d}s  sd ${sd_contrib:6.2f}   "
              f"(independence would say ${sd_indep:5.2f}, "
              f"{sd_contrib/max(sd_indep,1e-9):.1f}x understated)")

    # ---- walk real windows: at each tau, is the outcome decided?
    print("\nDECIDEDNESS on real proxy windows. A window is counted DECIDED at "
          "tau when\n  |partial average - strike| exceeds what the remaining "
          "observations can plausibly\n  do (2 sd of their contribution).")
    hdr = f"{'tau':>4s} {'windows':>8s} {'decided':>8s} {'%':>6s} {'median |z|':>11s}"
    print(hdr); print("-" * len(hdr))
    rows_out = {}
    for tau in (60, 30, 10, 5, 1):
        dec = tot = 0
        zs = []
        # a window needs 60 obs before T_start and 60 before T_end, 15 min apart
        step = 5
        for i in range(0, len(series) - (N_OBS + 900 + N_OBS), step):
            open_vals = vals[i:i + N_OBS]
            if len(open_vals) < N_OBS:
                break
            j = i + N_OBS + 900 - N_OBS          # start of closing average
            close_vals = vals[j:j + N_OBS]
            if len(close_vals) < N_OBS:
                break
            strike = sum(open_vals) / N_OBS
            k = N_OBS - tau
            partial = sum(close_vals[:k])
            st = PartialState(k=k, tau=tau, partial=partial, strike=strike,
                              source=SOURCE_PROXY)
            need = st.required_remaining_mean()
            m0 = st.elapsed_mean
            tot += 1
            if need is None or m0 is None:
                continue
            t = float(tau)
            sd = math.sqrt(sigma ** 2 * (t + 1.0) * (2.0 * t + 1.0) / (6.0 * t))
            z = abs(m0 - need) / max(sd, 1e-9)
            zs.append(z)
            if z > 2.0:
                dec += 1
        pct = 100.0 * dec / tot if tot else 0.0
        med = statistics.median(zs) if zs else 0.0
        rows_out[tau] = dict(windows=tot, decided=dec, pct=pct, median_z=med)
        print(f"{tau:4d} {tot:8d} {dec:8d} {pct:6.1f} {med:11.2f}")

    print("\nWhat this means for an estimator:")
    d10 = rows_out.get(10, {})
    print(f"  At tau=10s, 83.3% of the average's WEIGHT is fixed, and "
          f"{d10.get('pct', 0):.1f}% of windows are\n  decided beyond 2 sd. The "
          f"two numbers are not the same quantity and the first does\n  not "
          f"imply the second.")
    print("  The estimator's edge therefore lives in the windows that are NOT "
          "decided,\n  and in those windows it needs the PARTIAL AVERAGE -- "
          "which we do not have,\n  because we hold no BRTI observations at all.")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "inventory", "brti_determinacy.json")
    json.dump(dict(source=SOURCE_PROXY, note="Coinbase proxy; NOT a settlement "
                   "replay and not BRTI", per_second_sd=sigma,
                   observations=len(series), by_tau=rows_out),
              open(out, "w"), indent=1)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
