"""EMPIRICAL calibration of both settlement-average estimators on REAL BRTI.

Not a simulation. 20 hours of the venue's own 1-second BRTI series across
five separate days, fetched from the same free, unauthenticated endpoint
the production reference reads:

  GET /v1/asset-prices/history?assets=crypto:btc
      &bucket=ASSET_PRICE_BUCKET_1S&from=<epoch>&to=<epoch>

For each decision second t and horizon tau, both estimators forecast the
settlement average A(t+tau); sigma comes from the module's OWN
`sigma_per_second` over the preceding history, so this is the production
arithmetic end to end rather than a reimplementation.

CALIBRATION RATIO = realised residual sd / forecast sd.
  1.0  the forecast's spread is right
  <1   over-dispersed: too unsure, declines when it should not
  >1   under-dispersed: overconfident -- the dangerous direction

Run: PYTHONPATH=. python3 analysis/l5/btc5m/brti_calibration.py
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.getcwd())
from src import l5_btc_reference as BR          # noqa: E402

DIR = os.environ.get("BRTI_DIR", "")
TAUS = (5, 10, 15, 30, 45, 60, 120, 240)
STEP = 7            # decision seconds, coprime-ish stride to decorrelate


def load():
    out = []
    for f in sorted(glob.glob(os.path.join(DIR, "*.json"))):
        c = json.load(open(f))["series"][0]["candles"]
        out.append([(int(x["timestamp"]), float(x["close"])) for x in c
                    if int(x.get("tickCount", 0)) == BR.AVERAGING_S])
    return out


def run(exact: bool):
    if exact:
        os.environ[BR.EXACT_LATE_VAR_ENV] = BR.EXACT_LATE_VAR
    else:
        os.environ.pop(BR.EXACT_LATE_VAR_ENV, None)
    acc = {t: [] for t in TAUS}
    sds = {t: [] for t in TAUS}
    for series in load():
        by = dict(series)
        lo = BR.MIN_HISTORY_S + BR.AVERAGING_S + 5
        for i in range(lo, len(series) - max(TAUS) - 1, STEP):
            bucket, a_now = series[i]
            a_prev = by.get(bucket - 1)
            if a_prev is None:
                continue
            sig = BR.sigma_per_second(series[:i + 1], now_bucket=bucket)
            if sig is None:
                continue
            sig = max(sig, BR.SIGMA_REL_FLOOR * a_now) * BR.VOL_INFLATION
            for tau in TAUS:
                truth = by.get(bucket + tau)
                if truth is None:
                    continue
                d = BR.settlement_distribution(a_now=a_now, a_prev=a_prev,
                                               sigma=sig, seconds_left=tau)
                acc[tau].append(truth - d["mean"])
                sds[tau].append(d["sd"])
    return acc, sds


def table(label, acc, sds):
    print("\n%s" % label)
    print("  tau      n     bias($)   realised sd   forecast sd   CALIBRATION")
    out = {}
    for tau in TAUS:
        e, s = acc[tau], sds[tau]
        if len(e) < 50:
            continue
        m = statistics.fmean(e)
        rsd = statistics.stdev(e)
        fsd = statistics.fmean(s)
        out[tau] = rsd / fsd
        print("  %4d  %6d   %+8.3f   %11.3f   %11.3f   %10.3f"
              % (tau, len(e), m, rsd, fsd, rsd / fsd))
    return out


def main():
    if not DIR or not glob.glob(os.path.join(DIR, "*.json")):
        print("set BRTI_DIR to a directory of venue history responses")
        return
    a0, s0 = run(False)
    a1, s1 = run(True)
    r0 = table("CURRENT estimator (linear ramp below 60s)", a0, s0)
    r1 = table("CORRECTED estimator (exact conditional variance)", a1, s1)
    print("\nSIDE BY SIDE -- calibration ratio, 1.0 is right")
    print("  tau    current   corrected   verdict")
    for tau in TAUS:
        if tau not in r0:
            continue
        a, b = r0[tau], r1[tau]
        better = "corrected closer" if abs(b - 1) < abs(a - 1) - 1e-9 \
            else "current closer" if abs(a - 1) < abs(b - 1) - 1e-9 \
            else "identical"
        print("  %4d   %7.3f   %9.3f   %s" % (tau, a, b, better))


if __name__ == "__main__":
    main()
