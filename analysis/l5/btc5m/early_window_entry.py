"""Entry economics across the FIRST 240 SECONDS of real BTC5m windows.

All inputs real: the venue's own 1-second BRTI, its own `priceToBeat`, and
its own settlement (`outcomePrices`). 96 consecutive five-minute windows
over two days. No book is involved, so nothing here claims executability
-- it asks the prior question: does the index carry information at these
horizons that an entry rule could use?

Run: BRTI_DIR=... SETTLE_DIR=... PYTHONPATH=. python3 \
     analysis/l5/btc5m/early_window_entry.py
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.getcwd())
from src import l5_btc_reference as BR          # noqa: E402

BRTI_DIR = os.environ.get("BRTI_DIR", "")
SETTLE_DIR = os.environ.get("SETTLE_DIR", "")
#: tau values inside the first 240s of a 300s window (tau = 300 - elapsed)
TAUS = (295, 270, 240, 210, 180, 150, 120, 90, 60)


def load_index():
    pts = {}
    for f in sorted(glob.glob(os.path.join(BRTI_DIR, "*.json"))):
        for c in json.load(open(f))["series"][0]["candles"]:
            if int(c.get("tickCount", 0)) == BR.AVERAGING_S:
                pts[int(c["timestamp"])] = float(c["close"])
    return pts


def load_windows():
    out = []
    for f in sorted(glob.glob(os.path.join(SETTLE_DIR, "*.json"))):
        m = json.load(open(f))["market"]
        apt = m.get("assetPriceTerms") or {}
        px = m.get("outcomePrices")
        if not apt or not px:
            continue
        try:
            ws = dt.datetime.fromisoformat(
                apt["windowStart"].replace("Z", "+00:00")).timestamp()
            we = dt.datetime.fromisoformat(
                apt["windowEnd"].replace("Z", "+00:00")).timestamp()
            k = float(apt["priceToBeat"]["value"])
            yes = float(json.loads(px)[0] if isinstance(px, str) else px[0])
        except Exception:                                      # noqa: BLE001
            continue
        out.append({"slug": m["slug"], "ws": ws, "we": we, "k": k,
                    "up": yes >= 0.5})
    return out


def main():
    if not BRTI_DIR or not SETTLE_DIR:
        print("set BRTI_DIR and SETTLE_DIR")
        return
    pts = load_index()
    wins = load_windows()
    series_sorted = sorted(pts.items())
    rows = []
    for w in wins:
        settle_bucket = w["we"] - 1.0
        for tau in TAUS:
            bucket = int(settle_bucket - tau)
            if bucket not in pts or (bucket - 1) not in pts:
                continue
            hist = [(t, v) for t, v in series_sorted
                    if bucket - 700 <= t <= bucket]
            if len(hist) < BR.MIN_HISTORY_S:
                continue
            e = BR.probability_up(series=hist, strike=w["k"],
                                  window_end=w["we"], now=bucket + 1.4)
            if not e.get("ok"):
                continue
            rows.append({"tau": tau, "p": e["p_up"], "up": w["up"],
                         "dist": e["distance"], "sd": e["sd"],
                         "sig": e["sigma_used"],
                         "mom": e["raw_ahead_of_average"],
                         "z": e["z"], "slug": w["slug"]})
    print("windows %d, decision rows %d\n" % (len(wins), len(rows)))

    print("CALIBRATION BY TIME REMAINING  (is the index informative early?)")
    print("  tau    n   mean p_up   actual UP rate   |p-rate|   Brier")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["tau"]].append(r)
    for tau in TAUS:
        v = by.get(tau) or []
        if len(v) < 20:
            continue
        mp = statistics.fmean(r["p"] for r in v)
        ar = statistics.fmean(1.0 if r["up"] else 0.0 for r in v)
        br = statistics.fmean((r["p"] - (1.0 if r["up"] else 0.0)) ** 2
                              for r in v)
        print("  %4d %4d   %8.4f   %14.4f   %8.4f   %6.4f"
              % (tau, len(v), mp, ar, abs(mp - ar), br))

    print("\nDISCRIMINATION: does |z| separate winners from losers?")
    print("  tau    n   |z|<0.25   0.25-0.75    >0.75     (UP rate when p>0.5)")
    for tau in TAUS:
        v = by.get(tau) or []
        if len(v) < 20:
            continue
        cells = []
        for lo, hi in ((0, 0.25), (0.25, 0.75), (0.75, 99)):
            s = [r for r in v if lo <= abs(r["z"]) < hi]
            if not s:
                cells.append("     n/a")
                continue
            hit = statistics.fmean(
                1.0 if (r["up"] == (r["p"] > 0.5)) else 0.0 for r in s)
            cells.append("%5.1f%%(%d)" % (100 * hit, len(s)))
        print("  %4d %4d   %s" % (tau, len(v), "  ".join(cells)))

    print("\nA COIN FLIP IS 50%. Hit rate = how often the side the index")
    print("favours is the side that settles. Brier 0.25 is uninformative.")


if __name__ == "__main__":
    main()
