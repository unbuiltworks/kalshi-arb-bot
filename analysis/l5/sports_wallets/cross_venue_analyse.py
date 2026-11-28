"""Lead/lag between Polymarket US and international for the same games (file from cross_venue_sampler.py).
READ-ONLY arithmetic. Prices are for the first outcome. A sample is used only when both venues answered
within 2 s of each other and both books are two-sided with a spread <= 3c (live, quoted markets).
  1. crossed: how often one venue's ask is below the other's bid, and by how much;
  2. lead/lag: correlation of mid changes at lags (which venue moves first);
  3. warning: before the US mid drops >= 1c, was the international mid already below the US mid?
  4. take-the-lagging-US-side rule, valued at the US mid later, after the US taker fee (promo, net of rebate);
  5. resting-bid proxy: a US bid that sits ABOVE the international mid is 'exposed'; what does the US mid do next?
Modelled marks on public quotes - not fills, not P&L."""
import bisect, collections, json, sys
FEE = 0.0695 * 0.30                      # taker coefficient net of the 70% promo rebate (to 31 Dec)
rows = [json.loads(l) for l in open(sys.argv[1])][1:]
g = collections.defaultdict(list)
for r in rows:
    if None in (r.get("ib"), r.get("ia"), r.get("ub"), r.get("ua")) or r.get("us") != "MARKET_STATE_OPEN": continue
    if abs(r["ti"] - r["tu"]) > 2 or r["ia"] - r["ib"] > .03 or r["ua"] - r["ub"] > .03: continue
    if not (.05 <= r["ub"] <= .95): continue
    g[r["slug"]].append((r["tu"], r["ib"], r["ia"], r["ub"], r["ua"]))
n = sum(len(v) for v in g.values())
span = max(r.get("tu", 0) for r in rows) - min(r["tu"] for r in rows if "tu" in r)
print("usable samples %d over %d games, %.0f min" % (n, len(g), span / 60))
c1 = [(s, v) for s, x in g.items() for v in x]
us_cheap = [v for _, v in c1 if v[4] < v[1] - 1e-9]; us_rich = [v for _, v in c1 if v[3] > v[2] + 1e-9]
print("1. crossed: US ask below intl bid in %.2f%% of samples (mean gap %.4f); US bid above intl ask in %.2f%% (mean gap %.4f)" % (
    100 * len(us_cheap) / n, sum(v[1] - v[4] for v in us_cheap) / max(len(us_cheap), 1),
    100 * len(us_rich) / n, sum(v[3] - v[2] for v in us_rich) / max(len(us_rich), 1)))
d = [(v[1] + v[2]) / 2 - (v[3] + v[4]) / 2 for _, v in c1]
print("   intl mid minus US mid: mean %+.4f, mean abs %.4f, share with |diff| >= 1c: %.1f%%" % (sum(d) / n, sum(abs(x) for x in d) / n, 100 * sum(abs(x) >= .01 - 1e-9 for x in d) / n))
def series(x):
    return [v[0] for v in x], [(v[1] + v[2]) / 2 for v in x], [(v[3] + v[4]) / 2 for v in x]
def at(t, m, ts):
    k = bisect.bisect_right(t, ts) - 1
    return m[k] if k >= 0 and ts - t[k] < 20 else None
print("2. lead/lag: corr( intl change over 9 s ending at t , US change over 9 s ending at t+lag )")
for lag in (-60, -30, -15, -9, -6, -3, 0, 3, 6, 9, 15, 30, 60):
    xs, ys = [], []
    for s, x in g.items():
        t, mi, mu = series(x)
        for ts in t[::3]:
            a0, a1 = at(t, mi, ts - 9), at(t, mi, ts); b0, b1 = at(t, mu, ts + lag - 9), at(t, mu, ts + lag)
            if None not in (a0, a1, b0, b1): xs.append(a1 - a0); ys.append(b1 - b0)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)); vx = sum((a - mx) ** 2 for a in xs); vy = sum((b - my) ** 2 for b in ys)
    print("   lag %+4ds  corr %+.3f  (n %d)   [positive lag = US moves AFTER intl]" % (lag, cov / (vx * vy) ** .5 if vx and vy else 0, len(xs)))
warn = tot = 0; lead_us = tot2 = 0
for s, x in g.items():
    t, mi, mu = series(x)
    for i in range(4, len(t)):
        if mu[i] - mu[i - 1] <= -.01 + 1e-9 and t[i] - t[i - 1] < 10:
            tot += 1; warn += mi[i - 1] <= mu[i - 1] - .005 + 1e-9
        if mi[i] - mi[i - 1] <= -.01 + 1e-9 and t[i] - t[i - 1] < 10:
            tot2 += 1; lead_us += mu[i - 1] <= mi[i - 1] - .005 + 1e-9
print("3. US mid drops >=1c: %d events; international mid was already >=0.5c below the US mid one sample earlier in %.0f%%" % (tot, 100 * warn / max(tot, 1)))
print("   (mirror) intl mid drops >=1c: %d events; US mid already >=0.5c below in %.0f%%" % (tot2, 100 * lead_us / max(tot2, 1)))
print("4. take US ask when it is at or below the intl bid minus x; value at US mid later, net of taker fee")
print("   (one signal per game per 30 s; mean / median / share positive / equal-weight-by-game mean / games)")
for xgap in (0.0, 0.005, 0.01):
    for H in (60, 300, 900):
        per = collections.defaultdict(list); last = {}
        for s, x in g.items():
            t, mi, mu = series(x)
            for v in x:
                if v[4] <= v[1] - xgap + 1e-9 and v[0] - last.get(s, 0) > 30:
                    m = at(t, mu, v[0] + H)
                    if m is None or v[0] + H > t[-1]: continue
                    last[s] = v[0]; per[s].append(m - v[4] - FEE * v[4] * (1 - v[4]))
        allp = sorted(p for v in per.values() for p in v); k = len(allp)
        if not k: continue
        print("   gap>=%.3f +%4ds: mean %+.4f  median %+.4f  positive %2.0f%%  by-game %+.4f  (signals %d, games %d)" % (
            xgap, H, sum(allp) / k, allp[k // 2], 100 * sum(p > 0 for p in allp) / k,
            sum(sum(v) / len(v) for v in per.values()) / len(per), k, len(per)))
print("5. resting US bid vs intl mid: next move of the US mid, by where the US best bid sits relative to the intl mid")
for H in (60, 300):
    b = collections.defaultdict(lambda: [0.0, 0])
    for s, x in g.items():
        t, mi, mu = series(x)
        for v in x[::5]:
            m = at(t, mu, v[0] + H)
            if m is None or v[0] + H > t[-1]: continue
            rel = v[3] - (v[1] + v[2]) / 2
            k = "bid >= intl mid+0.5c" if rel >= .005 - 1e-9 else "bid ~ intl mid" if rel > -.005 + 1e-9 else "bid 0.5-1c below" if rel > -.015 + 1e-9 else "bid >1c below"
            b[k][0] += m - v[3]; b[k][1] += 1
    for k in ("bid >= intl mid+0.5c", "bid ~ intl mid", "bid 0.5-1c below", "bid >1c below"):
        if b[k][1]: print("   +%3ds  %-22s later US mid minus that bid: %+.4f  (n %d)" % (H, k, b[k][0] / b[k][1], b[k][1]))
