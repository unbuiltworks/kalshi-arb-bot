"""Size available when Polymarket US lags: for each signal (US ask at least x below the international bid, one per
game per 30 s), contracts at the US best ask, contracts at the international best bid, and modelled dollars for a
buyer limited to min(size at ask, CAP) contracts, marked at the US mid 60 s later, net of the promo taker fee.
READ-ONLY, quotes not fills. usage: cross_venue_sizes.py <file from cross_venue_sampler.py with sizes>"""
import bisect, collections, json, sys
FEE = 0.0695 * 0.30
rows = [json.loads(l) for l in open(sys.argv[1])][1:]
g = collections.defaultdict(list)
for r in rows:
    if None in (r.get("ib"), r.get("ia"), r.get("ub"), r.get("ua")) or r.get("us") != "MARKET_STATE_OPEN": continue
    if abs(r["ti"] - r["tu"]) > 2 or r["ia"] - r["ib"] > .03 or r["ua"] - r["ub"] > .03 or not (.05 <= r["ub"] <= .95): continue
    g[r["slug"]].append(r)
mins = (max(r["tu"] for v in g.values() for r in v) - min(r["tu"] for v in g.values() for r in v)) / 60
print("games %d, %.0f min" % (len(g), mins))
for gap in (0.005, 0.01):
    sig = []
    for s, x in g.items():
        t = [r["tu"] for r in x]; mu = [(r["ub"] + r["ua"]) / 2 for r in x]; last = 0
        for i, r in enumerate(x):
            if r["ua"] <= r["ib"] - gap + 1e-9 and r["tu"] - last > 30:
                k = bisect.bisect_right(t, r["tu"] + 60) - 1
                if r["tu"] + 60 > t[-1] or r["tu"] + 60 - t[k] > 20: continue
                last = r["tu"]
                j = i
                while j + 1 < len(x) and x[j + 1]["ua"] <= x[j + 1]["ib"] - gap + 1e-9: j += 1
                sig.append((s, r["uaq"], r["ibq"], mu[k] - r["ua"] - FEE * r["ua"] * (1 - r["ua"]), x[j]["tu"] - r["tu"], r["ua"]))
    n = len(sig)
    if not n: print("gap %.3f: no signals" % gap); continue
    q = sorted(v[1] for v in sig); e = sorted(v[3] for v in sig); d = sorted(v[4] for v in sig)
    print("gap>=%.3f: %d signals in %d games (%.1f an hour)" % (gap, n, len({v[0] for v in sig}), n / mins * 60))
    print("  edge a share at +60 s: mean %+.4f median %+.4f positive %.0f%%" % (sum(e) / n, e[n // 2], 100 * sum(v > 0 for v in e) / n))
    print("  contracts at the US ask: p10 %.0f p25 %.0f median %.0f p75 %.0f p90 %.0f" % tuple(q[int(p * (n - 1))] for p in (.1, .25, .5, .75, .9)))
    print("  contracts at the intl bid: median %.0f" % sorted(v[2] for v in sig)[n // 2])
    print("  how long the gap stayed open: p25 %.0fs median %.0fs p75 %.0fs" % tuple(d[int(p * (n - 1))] for p in (.25, .5, .75)))
    for cap in (10, 50, 200, 1000):
        tot = sum(min(v[1], cap) * v[3] for v in sig); cost = sum(min(v[1], cap) * v[5] for v in sig)
        print("  buyer capped at %4d contracts a signal: modelled $%.2f over the recording ($%.2f an hour) on $%.0f bought" % (cap, tot, tot / mins * 60, cost))
    byg = collections.defaultdict(float)
    for v in sig: byg[v[0]] += min(v[1], 200) * v[3]
    top = sorted(byg.items(), key=lambda x: -abs(x[1]))[:5]
    print("  largest games at cap 200:", ", ".join("%s $%.2f" % (k[:22], v) for k, v in top))
