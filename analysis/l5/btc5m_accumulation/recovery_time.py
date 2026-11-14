"""Does a 5m leg's value depend on HOW MUCH WINDOW IS LEFT when it fills?

THE HYPOTHESIS, and where it came from. The 15m shadow shows a lone leg
winning 27.8% of the time against 2.5% on 5m. The plausible mechanism is
not window length as such but RECOVERY TIME: three times the window gives
a collapsed side three times as long to come back.

If that is the mechanism, it must also operate WITHIN a 5-minute window.
A leg filled at t=30s has four minutes to recover; one filled at t=240s
has thirty seconds and is nearly dead on arrival. So:

  (a) the lone-leg win rate should FALL with fill time, and
  (b) refusing late fills -- resting only until some cutoff -- should pay.

(b) is actionable and is NOT the already-closed "delay entry past the
toxic first minute", which is the opposite move and destroyed pair legs.
This keeps the early window and discards the tail.

Causal: the cutoff uses only elapsed time, never the outcome.
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
    w = defaultdict(lambda: {"outcome": None, "rows": []})
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
            db = (d.get("down") or {}).get("bid")
            t = d.get("t_in_window_s")
            if ub is None or db is None or t is None:
                continue
            rec["rows"].append((float(t), float(ub), float(db)))
    out = {}
    for m, r in w.items():
        if r["outcome"] in ("UP", "DOWN") and r["rows"]:
            r["rows"].sort(key=lambda x: x[0])
            out[m] = r
    return out


def play(rec, stop_resting_at=None):
    """Rest 0.08 both sides; a side fills when its bid falls through.
    `stop_resting_at` cancels both orders at that t. Returns (pnl, case,
    first_fill_t)."""
    held = {"UP": 0, "DOWN": 0}
    cost = 0.0
    first_t = None
    for (t, ub, db) in rec["rows"]:
        if stop_resting_at is not None and t > stop_resting_at:
            break
        for side, bid in (("UP", ub), ("DOWN", db)):
            if held[side] == 0 and bid <= ENTRY + 1e-12:
                held[side] = 1
                cost += ENTRY
                if first_t is None:
                    first_t = t
    pnl = float(held[rec["outcome"]]) - cost
    case = ("BOTH" if held["UP"] and held["DOWN"]
            else "ONE" if held["UP"] or held["DOWN"] else "NONE")
    return pnl, case, first_t


def stats(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return 100.0 * m, t


def main() -> int:
    w = load()
    print("windows: %d\n" % len(w))

    # (a) descriptive: lone-leg outcome by WHEN it filled
    print("(a) ONE-sided windows, by when the single leg filled")
    print("%14s %7s %7s %8s" % ("fill time", "n", "wins", "win rate"))
    buckets = [(0, 60), (60, 120), (120, 180), (180, 240), (240, 300)]
    for lo, hi in buckets:
        n = wins = 0
        for rec in w.values():
            pnl, case, ft = play(rec)
            if case != "ONE" or ft is None or not (lo <= ft < hi):
                continue
            n += 1
            if pnl > 0:
                wins += 1
        if n:
            print("%6d-%-6ds %7d %7d %7.1f%%" % (lo, hi, n, wins, 100.0*wins/n))
        else:
            print("%6d-%-6ds %7d %7s %8s" % (lo, hi, n, "-", "-"))

    # (b) actionable: stop resting at a cutoff
    print("\n(b) stop resting at a cutoff (keeps the early window)")
    print("%10s %7s %7s %7s %9s %8s" %
          ("cutoff", "BOTH", "ONE", "NONE", "net c/w", "t"))
    for cut in (None, 240, 210, 180, 150, 120, 90, 60):
        series = []
        cases = {"BOTH": 0, "ONE": 0, "NONE": 0}
        for rec in w.values():
            pnl, case, _ = play(rec, cut)
            series.append(pnl)
            cases[case] += 1
        m, t = stats(series)
        print("%10s %7d %7d %7d %+9.2f %+8.2f"
              % ("none" if cut is None else "%ds" % cut,
                 cases["BOTH"], cases["ONE"], cases["NONE"], m, t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
