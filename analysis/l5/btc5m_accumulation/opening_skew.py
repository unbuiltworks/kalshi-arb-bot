"""Does WHERE THE WINDOW OPENS predict the pair rate? READ-ONLY.

THE MECHANISM, stated before looking. A pair needs the price to reach
BOTH 0.08 tails. A window opening at 50/50 is equidistant from both. One
opening at 70/30 is already near one tail and far from the other, which
is the signature of a market that has already picked a direction. So
|opening - 0.50| should predict pairing NEGATIVELY.

WHY THIS IS NOT ALREADY TESTED. `regime_filter.py` used the PREVIOUS
window's price path (chop, flips, prior pair, depth). `vol_regime.py`
used the underlying's realized volatility. Neither used the opening
quote of the window being traded, which is the most direct statement of
how far the price has to travel.

AND IT IS ACTIONABLE. The opening quote is visible before any order is
placed, so a verdict here supports "rest this window or sit it out" --
the same decision regime_filter was looking for and did not find.

ONE hypothesis, stated in advance, tested as a correlation. Not a sweep.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict

TAPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "btc5m_aligned_20261112.jsonl.gz")
CANCEL_LEAD_S = 30.0
ENTRY = 0.08


def load():
    w = defaultdict(lambda: {"outcome": None, "rows": [], "open_up": None,
                             "open_t": None})
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
            ub = (d.get("up") or {}).get("bid")
            ua = (d.get("up") or {}).get("ask")
            db = (d.get("down") or {}).get("bid")
            t = d.get("t_in_window_s")
            if ub is None or db is None or t is None:
                continue
            t = float(t)
            # the EARLIEST quote we hold for this window is its opening mark
            if rec["open_t"] is None or t < rec["open_t"]:
                rec["open_t"] = t
                mid = (float(ub) + float(ua)) / 2.0 if ua is not None else float(ub)
                rec["open_up"] = mid
            rec["rows"].append((float(ub), float(db)))
    return {m: r for m, r in w.items()
            if r["outcome"] in ("UP", "DOWN") and r["rows"]
            and r["open_up"] is not None}


def play(rec):
    held = {"UP": 0, "DOWN": 0}
    cost = 0.0
    for (ub, db) in rec["rows"]:
        for side, bid in (("UP", ub), ("DOWN", db)):
            if held[side] == 0 and bid <= ENTRY + 1e-12:
                held[side] = 1
                cost += ENTRY
    return float(held[rec["outcome"]]) - cost, bool(held["UP"] and held["DOWN"])


def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan"), float("nan")
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1 else float("nan")
    return r, t


def main() -> int:
    w = load()
    rows = []
    for rec in w.values():
        pnl, paired = play(rec)
        skew = abs(rec["open_up"] - 0.50)
        rows.append((skew, pnl, 1.0 if paired else 0.0, rec["open_t"]))
    print("windows: %d   (opening mark taken at median t=%.1fs)"
          % (len(rows), sorted(r[3] for r in rows)[len(rows) // 2]))
    print()
    sk = [r[0] for r in rows]
    print("opening |p-0.50|: min %.3f  median %.3f  max %.3f"
          % (min(sk), sorted(sk)[len(sk) // 2], max(sk)))
    print()
    print("THE PRE-STATED TEST (one correlation, bar is 2.0):")
    for nm, idx in (("net pnl", 1), ("paired (0/1)", 2)):
        r, t = corr(sk, [x[idx] for x in rows])
        print("  corr(opening skew, %-13s) = %+.4f   t = %+.2f" % (nm, r, t))
    print()
    print("descriptive only, for shape -- NOT a second test:")
    rows.sort()
    q = len(rows) // 3
    for nm, chunk in (("most centred", rows[:q]),
                      ("middle", rows[q:2 * q]),
                      ("most skewed", rows[2 * q:])):
        pn = [x[1] for x in chunk]
        pr = sum(x[2] for x in chunk)
        m = sum(pn) / len(pn)
        print("  %-13s n %3d   pair %4.1f%%   net %+6.2f c/w"
              % (nm, len(chunk), 100.0 * pr / len(chunk), 100 * m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
