"""Is a 0.08 maker rung ever REACHED inside a real BTC5m window?

WHY THIS IS THE DECIDING MEASUREMENT. Production rests BOTH straddle legs
at a fixed 0.08 limit (`L5_BTC5M_STRADDLE_PRICE`, book-relative OFF),
observed live on 2026-11-17 at 21:46-21:47Z with the market at 0.50-0.67:

    entry {price: "0.08", qty: "0.10"}   bid 0.5000 / ask 0.5100
    entry {price: "0.08", qty: "0.07"}   bid 0.6600 / ask 0.6700

A resting bid fills only if the price comes to it. So before any capital
is committed, the question is not "is the EV gate satisfied" -- it is
"does UP (or DOWN) ever trade near 0.08 inside five minutes".

METHOD, using the SAME model production prices with. The strike of a
BTC5m window is the 60-second BRTI average at its start. For every second
of the window the model gives p_up; the venue's UP price tracks that
probability. A 0.08 bid on UP is reachable iff p_up falls to <= 0.08
during the window, and the DOWN leg iff p_up rises to >= 0.92.

NO FILL PROBABILITY IS INVENTED. This measures REACHABILITY, which is a
NECESSARY condition for a fill and not a sufficient one: being touched is
not being filled, because the queue ahead of us is not modelled. An
unreachable rung, though, cannot fill at all -- and that is the finding
this is built to establish or refute.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_btc_reference as BR      # noqa: E402

WINDOW_S = 300
#: seconds of history the volatility estimator needs behind each window
HIST_S = 900
AV = BR.AVERAGING_S
RUNG = 0.08


def candles(path):
    d = json.load(open(path))
    out = []
    for s in d.get("series") or []:
        for c in s.get("candles") or []:
            v = c.get("close", c.get("open"))
            if v is None:
                continue
            try:
                out.append((int(c["timestamp"]), float(v)))
            except (TypeError, ValueError, KeyError):
                continue
    return sorted(set(out))


def main(path):
    rows = candles(path)
    if len(rows) < 2 * WINDOW_S:
        print("too little data: %d candles" % len(rows))
        return
    by_t = dict(rows)
    t0, t1 = rows[0][0], rows[-1][0]
    print("BRTI 1s candles: %d  span %ds (%.1f h)" % (len(rows), t1 - t0, (t1 - t0) / 3600.0))
    print("model: the production settlement-average forecast; rung = %.2f" % RUNG)
    print()

    starts = [t for t in range(t0 + HIST_S, t1 - WINDOW_S, WINDOW_S) if t in by_t]
    hdr = "%-22s %8s %8s %8s %8s %8s" % ("window start (epoch)", "p_min", "p_max",
                                         "UP<=.08", "DN<=.08", "either")
    print(hdr); print("-" * len(hdr))
    n = up_hit = dn_hit = either = 0
    pmins, pmaxs = [], []
    for st in starts:
        #: THE VOLATILITY ESTIMATOR NEEDS REAL HISTORY, not the 60 seconds
        #: the strike needs. Feeding it only the averaging window returned
        #: TOO_LITTLE_HISTORY_FOR_VOLATILITY on every call and the first
        #: version of this script silently measured nothing.
        hist = [(t, by_t[t]) for t in range(st - HIST_S, st + WINDOW_S + 1)
                if t in by_t]
        if len(hist) < HIST_S + WINDOW_S - 30:
            continue
        pre = [v for t, v in hist if st - AV <= t < st]
        if len(pre) < AV - 2:
            continue
        strike = sum(pre) / float(len(pre))
        end = st + WINDOW_S
        ps = []
        for t, _ in hist:
            #: `window_end` must stay strictly ahead of `now`, or the model
            #: reports WINDOW_OVER rather than a probability.
            if t < st or t >= end:
                continue
            r = BR.probability_up(series=[(a, b) for a, b in hist if a <= t],
                                  strike=strike, window_end=end, now=float(t))
            if r.get("ok"):
                ps.append(float(r["p_up"]))
        if len(ps) < WINDOW_S // 2:
            continue
        n += 1
        lo, hi = min(ps), max(ps)
        pmins.append(lo); pmaxs.append(hi)
        u = lo <= RUNG
        d = hi >= 1.0 - RUNG
        up_hit += u; dn_hit += d; either += (u or d)
        if n <= 12:
            print("%-22d %8.4f %8.4f %8s %8s %8s"
                  % (st, lo, hi, "YES" if u else "no", "YES" if d else "no",
                     "YES" if (u or d) else "no"))
    if not n:
        print("no complete windows in range")
        return
    print()
    print("=" * 64)
    print("windows measured            : %d" % n)
    print("UP rung 0.08 reached        : %d (%.1f%%)" % (up_hit, 100.0 * up_hit / n))
    print("DOWN rung 0.08 reached      : %d (%.1f%%)" % (dn_hit, 100.0 * dn_hit / n))
    print("EITHER leg reachable        : %d (%.1f%%)" % (either, 100.0 * either / n))
    print("BOTH legs (a real pair)     : %d (%.1f%%)"
          % (up_hit + dn_hit - either, 100.0 * (up_hit + dn_hit - either) / n))
    print()
    print("median p_min over windows   : %.4f" % sorted(pmins)[len(pmins) // 2])
    print("median p_max over windows   : %.4f" % sorted(pmaxs)[len(pmaxs) // 2])
    print("closest any window came to the UP rung : %.4f" % min(pmins))
    print("closest any window came to the DOWN rung: %.4f" % (1.0 - max(pmaxs)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/brti.json")
