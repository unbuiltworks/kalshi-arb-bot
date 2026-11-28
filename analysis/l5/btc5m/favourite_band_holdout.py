"""Hold-out check of one candidate from bonereaper_spot_discrepancy.py:
on our venue, 2026-11-17..20, buying the favourite when its ask was
0.50-0.85 paid before and after our taker fee. Same rule, other days.

Rule (fixed before looking at these days): at each ~10 s sample between
10 s and 280 s, if a side's ask is in the band, buy it at the NEXT
sample's ask. Outcome: next window's price-to-beat >= this one's = UP.
USD per share, mean of per-window means, 95% bootstrap over windows.
"""
import collections
import datetime as dt
import json
import random
import sys

K = 0.095


def boot(v, n=3000, seed=5):
    if len(v) < 8:
        return None
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def main():
    q = collections.defaultdict(set)
    tb = {}
    for path in sys.argv[1:]:
        for line in open(path):
            r = json.loads(line)
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                continue
            q[r["slug"]].add((float(r["t"]), float(r["bid"]),
                              float(r["ask"])))

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")

    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, rows in q.items():
        if s not in tb or nxt(s) not in tb:
            continue
        res = "UP" if tb[nxt(s)] >= tb[s] else "DOWN"
        rows = sorted(rows)
        for i, (t, b, a) in enumerate(rows[:-1]):
            if not (10 <= t <= 280):
                continue
            _t, nb, na = rows[i + 1]
            for oc, now, px in (("UP", a, na), ("DOWN", 1 - b, 1 - nb)):
                for lo, hi in ((.50, .70), (.70, .85), (.50, .85),
                               (.85, .95), (.95, 1.0)):
                    if lo <= now < hi and 0.01 < px < 0.995:
                        w = 1.0 if res == oc else 0.0
                        per[(s[-16:-6], lo, hi)][s].append(
                            (w - px, w - px - K * px * (1 - px)))
    days = sorted({k[0] for k in per})
    groups = [(d, [d]) for d in days]
    groups.append(("11-11..16", [d for d in days if d < "2026-11-17"]))
    groups.append(("11-17..20", [d for d in days if d >= "2026-11-17"]))
    for lo, hi in ((.50, .70), (.70, .85), (.50, .85), (.85, .95),
                   (.95, 1.0)):
        print("\nask %.2f-%.2f" % (lo, hi))
        for name, ds in groups:
            pre, net = [], []
            for d in ds:
                for rows in per.get((d, lo, hi), {}).values():
                    pre.append(sum(x for x, _ in rows) / len(rows))
                    net.append(sum(y for _, y in rows) / len(rows))
            if not pre:
                continue
            c1, c2 = boot(pre), boot(net)
            print("   %-11s windows %4d   before fee %+.4f %-17s after fee "
                  "%+.4f %s" % (name, len(pre), sum(pre) / len(pre),
                                "[%+.3f,%+.3f]" % c1 if c1 else "",
                                sum(net) / len(net),
                                "[%+.3f,%+.3f]" % c2 if c2 else ""))


if __name__ == "__main__":
    main()
