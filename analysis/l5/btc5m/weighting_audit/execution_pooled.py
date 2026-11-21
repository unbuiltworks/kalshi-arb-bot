"""Same signal, two ways to execute it: which keeps more of the edge?

SIGNAL (unchanged from favourite_band_holdout.py): at a ~10 s sample
between 10 s and 280 s, a side's ask is 0.50-0.70. The order goes live at
the NEXT sample (about 10 s of delay for both methods).

TAKER   buy at that next sample's ask. Fee 0.095 x p x (1 - p). Always
        fills (size at the ask is NOT recorded: one share assumed).
MAKER   rest a bid from that next sample for HOLD further samples, then
        cancel. Fee 0. Price is the side's bid then (JOIN) or one cent
        above it, never at or over the ask (IMPROVE).
        The recorder has no trades, so a fill is inferred two ways:
          STRICT  the ask later reaches our price (the market came
                  through us). Certain fill, worst selection.
          TOUCH   STRICT, or the bid later drops below our price (our
                  level was hit and cleared). Upper bound on fills.
        The truth lies between.
MAKER+T MAKER, and if not filled at cancel, buy at the ask then (fee).

Every number is USD per SIGNAL (an unfilled maker order counts as zero),
so the methods are directly comparable; "per fill" is also shown. Held to
settlement; outcome = next window's price-to-beat >= this one's.
Mean of per-window means, 95% bootstrap over windows.
"""
import collections
import datetime as dt
import gzip
import json
import random
import sys

K = 0.095
LO, HI = 0.50, 0.70


def boot(v, n=3000, seed=11):
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]



def _pool(pairs, n=2000, seed=7):
    import random as _r
    pairs = [p for p in pairs if p[1] > 0]
    if not pairs:
        return None
    k = len(pairs)
    pt = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
    if k < 8:
        return pt, (float("nan"), float("nan")), k
    rng = _r.Random(seed)
    ms = []
    for _ in range(n):
        sa = sb = 0.0
        for _i in range(k):
            a, b = pairs[rng.randrange(k)]
            sa += a; sb += b
        ms.append(sa / sb)
    ms.sort()
    return pt, (ms[int(.025 * n)], ms[int(.975 * n)]), k


def main():
    q = collections.defaultdict(set)
    tb = {}
    for line in gzip.open(sys.argv[1], "rt"):
        r = json.loads(line)
        if r.get("to_beat") is not None:
            tb.setdefault(r["slug"], float(r["to_beat"]))
        if r.get("closed") or r.get("bid") is None or r.get("ask") is None:
            continue
        q[r["slug"]].add((float(r["t"]), float(r["bid"]), float(r["ask"])))

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")

    def side(row, oc):                    # (bid, ask) of that side
        _t, b, a = row
        return (b, a) if oc == "UP" else (round(1 - a, 4), round(1 - b, 4))

    methods = ["TAKER"]
    for hold in (1, 3, 6):
        for px in ("JOIN", "IMPROVE"):
            for fill in ("STRICT", "TOUCH"):
                methods.append("MAKER %-7s hold %2ds %-6s" % (
                    px, hold * 10, fill))
                methods.append("MAKER+T %-5s hold %2ds %-6s" % (
                    px, hold * 10, fill))
    # per[(period, method)][window] -> list of (value per signal, filled,
    #                                           value if filled)
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, rows in q.items():
        if s not in tb or nxt(s) not in tb:
            continue
        res = "UP" if tb[nxt(s)] >= tb[s] else "DOWN"
        period = "HOLD-OUT 11-11..16" if s[-16:-6] < "2026-11-17" \
            else "FOUND ON 11-17..20"
        rows = sorted(rows)
        for i, row in enumerate(rows[:-1]):
            if not (10 <= row[0] <= 280):
                continue
            for oc in ("UP", "DOWN"):
                if not (LO <= side(row, oc)[1] < HI):
                    continue
                w = 1.0 if res == oc else 0.0
                b1, a1 = side(rows[i + 1], oc)
                if not (0.01 < a1 < 0.995):
                    continue
                for key in (period, "ALL TEN DAYS"):
                    v = w - a1 - K * a1 * (1 - a1)
                    per[(key, "TAKER")][s].append((v, 1, v))
                for hold in (1, 3, 6):
                    later = rows[i + 2:i + 2 + hold]
                    for pxn in ("JOIN", "IMPROVE"):
                        p = b1 if pxn == "JOIN" else \
                            min(round(b1 + 0.01, 4), round(a1 - 0.01, 4))
                        if not (0.01 < p < 0.99):
                            continue
                        for fn in ("STRICT", "TOUCH"):
                            hit = False
                            for r2 in later:
                                b2, a2 = side(r2, oc)
                                if a2 <= p + 1e-9 or (
                                        fn == "TOUCH" and b2 < p - 1e-9):
                                    hit = True
                                    break
                            m = "MAKER %-7s hold %2ds %-6s" % (
                                pxn, hold * 10, fn)
                            mt = "MAKER+T %-5s hold %2ds %-6s" % (
                                pxn, hold * 10, fn)
                            fb = None
                            if not hit and later:
                                af = side(later[-1], oc)[1]
                                if 0.01 < af < 0.995:
                                    fb = w - af - K * af * (1 - af)
                            for key in (period, "ALL TEN DAYS"):
                                per[(key, m)][s].append(
                                    (w - p if hit else 0.0, int(hit),
                                     w - p))
                                per[(key, mt)][s].append(
                                    (w - p if hit else (fb or 0.0),
                                     int(hit or fb is not None), 0.0))
    for period in ("FOUND ON 11-17..20", "HOLD-OUT 11-11..16",
                   "ALL TEN DAYS"):
        print("\n== %s ==" % period)
        print("   %-34s %8s  %-32s %s" % ("method", "fill rate",
                                          "USD per signal [95%]",
                                          "USD per maker fill"))
        for m in methods:
            d = per.get((period, m))
            if not d:
                continue
            v = [(sum(x for x, _, _ in r), len(r)) for r in d.values()]
            n = sum(len(r) for r in d.values())
            f = sum(h for r in d.values() for _, h, _ in r)
            _pt, (lo, hi), _k = _pool(v)
            pf = ""
            if m.startswith("MAKER ") and f:
                pf = "%+.4f" % (sum(y for r in d.values()
                                    for _, h, y in r if h) / f)
            print("   %-34s %7.1f%%  %+.4f [%+.3f,%+.3f] w=%4d   %s" % (
                m, 100.0 * f / n, _pt, lo, hi, len(v), pf))


if __name__ == "__main__":
    main()
