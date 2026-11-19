"""Does BTC's own realized volatility predict the pair rate? READ-ONLY.

THE GAP THIS FILLS. `regime_filter.py` asked whether the pair rate is
predictable and found nothing clearing its bar -- but every predictor it
tried was derived from the previous window's OWN UP PRICE (chop, flips,
whether it paired, how deep the loser got). It never looked at the
underlying.

That leaves the most mechanical predictor untested. A pair requires the
price to travel from 0.08 to ~0.92 AND BACK inside five minutes, which
requires a large round trip in BTC itself. So BTC's realized volatility
should predict the pair rate almost by construction -- and unlike the
price-path predictors it is measurable before the window opens.

NO LOOKAHEAD. Volatility is computed from index ticks STRICTLY BEFORE the
window's start, and used to call that window. The decision it would
support is "rest this window or sit it out", taken before any order.

Usage: python3 -I analysis/l5/btc5m_accumulation/vol_regime.py
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(HERE, "btc5m_aligned_20261112.jsonl.gz")
INDEX = os.path.join(HERE, "btc_index_1s_20261112.jsonl.gz")
CANCEL_LEAD_S = 30.0
ENTRY = 0.08
LOOKBACK_S = 300.0          # realized vol over the 5 minutes before the open


def load_index():
    ticks = []
    with gzip.open(INDEX, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
                ticks.append((float(d["t"]), float(d["a"])))
            except (ValueError, KeyError, TypeError):
                continue
    ticks.sort()
    return ticks


def load_windows():
    w = defaultdict(lambda: {"outcome": None, "rows": [], "start": None})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt, rem = d.get("market"), d.get("seconds_remaining")
            if not mkt or rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            if d.get("settled_outcome"):
                rec["outcome"] = str(d["settled_outcome"])
            ws = d.get("window_start_utc")
            if ws and rec["start"] is None:
                import datetime as dt
                rec["start"] = dt.datetime.fromisoformat(
                    ws.replace("Z", "+00:00")).timestamp()
            ub = (d.get("up") or {}).get("bid")
            db = (d.get("down") or {}).get("bid")
            if ub is None or db is None:
                continue
            rec["rows"].append((float(ub), float(db)))
    return {m: r for m, r in w.items()
            if r["outcome"] in ("UP", "DOWN") and r["rows"] and r["start"]}


def realized_vol(ticks, t_end, lookback):
    """Stdev of 1s log returns over [t_end-lookback, t_end). Strictly
    before the window opens."""
    lo = t_end - lookback
    seg = [a for (t, a) in ticks if lo <= t < t_end]
    if len(seg) < 30:
        return None
    rets = [math.log(seg[i] / seg[i - 1])
            for i in range(1, len(seg)) if seg[i - 1] > 0]
    if len(rets) < 20:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))


def outcome(rec):
    held = {"UP": 0, "DOWN": 0}
    cost = 0.0
    for (ub, db) in rec["rows"]:
        for side, bid in (("UP", ub), ("DOWN", db)):
            if held[side] == 0 and bid <= ENTRY + 1e-12:
                held[side] = 1
                cost += ENTRY
    pnl = float(held[rec["outcome"]]) - cost
    both = bool(held["UP"] and held["DOWN"])
    return pnl, both


def stats(xs):
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan"), n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return 100.0 * m, t, n


def main() -> int:
    ticks = load_index()
    wins = load_windows()
    print("index ticks %d   windows %d" % (len(ticks), len(wins)))
    rows = []
    for mkt, rec in wins.items():
        v = realized_vol(ticks, rec["start"], LOOKBACK_S)
        if v is None:
            continue
        pnl, both = outcome(rec)
        rows.append((v, pnl, both))
    rows.sort()
    print("windows with a usable prior-vol reading: %d\n" % len(rows))
    if len(rows) < 20:
        print("too few to split")
        return 0
    q = len(rows) // 4
    names = ("Q1 calmest", "Q2", "Q3", "Q4 most volatile")
    print("%-18s %7s %9s %10s %9s" % ("prior 5m vol", "n", "pair%", "net c/w", "t"))
    for i, nm in enumerate(names):
        chunk = rows[i * q:(i + 1) * q] if i < 3 else rows[3 * q:]
        pn = [p for (_, p, _) in chunk]
        pr = sum(1 for (_, _, b) in chunk if b)
        m, t, n = stats(pn)
        print("%-18s %7d %8.1f%% %+10.2f %+9.2f" % (nm, n, 100.0*pr/n, m, t))
    print()
    allp = [p for (_, p, _) in rows]
    m, t, n = stats(allp)
    print("all windows: net %+.2f c/w  t %+.2f  n %d" % (m, t, n))
    print()
    print("4 quartiles tested -> bar is sqrt(2 ln 4) = %.2f" % math.sqrt(2*math.log(4)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
