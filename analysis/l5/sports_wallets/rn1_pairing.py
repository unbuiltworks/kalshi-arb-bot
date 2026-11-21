"""How does RN1 get both sides below $1? For every condition where both outcomes were bought inside the
file: time between the two sides' buys, pair cost, and how interleaved the buying is. READ-ONLY."""
import gzip, collections, json, sys
rows = [json.loads(l) for l in (gzip.open(sys.argv[1],'rt') if sys.argv[1].endswith('.gz') else open(sys.argv[1])) if '"TRADE"' in l]
c = collections.defaultdict(list)
for r in rows: c[r["condition_id"]].append(r)
out = []
for k, v in c.items():
    v.sort(key=lambda r: r["timestamp"])
    s = collections.defaultdict(lambda: [0.0, 0.0, None, None])
    for r in v:
        x = s[r["outcome_index"]]; x[0] += r["size"]; x[1] += r["usdc_size"]
        x[2] = r["timestamp"] if x[2] is None else x[2]; x[3] = r["timestamp"]
    if len(s) < 2: continue
    a, b = list(s.values())[:2]
    q = min(a[0], b[0]); pc = a[1] / a[0] + b[1] / b[0]
    switches = sum(1 for x, y in zip(v, v[1:]) if x["outcome_index"] != y["outcome_index"])
    # nearest-opposite gap for each trade
    gaps = []
    for i, r in enumerate(v):
        opp = [abs(o["timestamp"] - r["timestamp"]) for o in v if o["outcome_index"] != r["outcome_index"]]
        gaps.append((min(opp), r["usdc_size"]))
    out.append(dict(slug=v[0]["slug"], q=q, pc=pc, first_gap=abs(a[2] - b[2]), n=len(v), switches=switches,
                    span=v[-1]["timestamp"] - v[0]["timestamp"], gross=q * (1 - pc), gaps=gaps,
                    resid=abs(a[0] - b[0]), tot=a[0] + b[0]))
print("both-sided conditions", len(out), " matched", round(sum(o["q"] for o in out)), " locked gross", round(sum(o["gross"] for o in out)))
print("unmatched shares as share of all shares in these conditions: %.1f%%" % (100 * sum(o["resid"] for o in out) / sum(o["tot"] for o in out)))
def pct(xs, p): xs = sorted(xs); return xs[int(p * (len(xs) - 1))]
fg = [o["first_gap"] for o in out]
print("gap between first buy of each side, seconds: p10 %d p25 %d p50 %d p75 %d p90 %d" % tuple(pct(fg, p) for p in (.1, .25, .5, .75, .9)))
allg = [g for o in out for g in o["gaps"]]; tu = sum(u for _, u in allg)
for lim in (2, 10, 60, 600, 3600):
    print("  usdc bought within %5ds of a buy on the other side: %.1f%%" % (lim, 100 * sum(u for g, u in allg if g <= lim) / tu))
print("pair cost distribution (weighted by matched shares):")
tq = sum(o["q"] for o in out)
for lo, hi in ((0, .9), (.9, .95), (.95, .98), (.98, .995), (.995, 1.005), (1.005, 1.02), (1.02, 9)):
    sel = [o for o in out if lo <= o["pc"] < hi]
    print("  %.3f-%.3f  n %4d  matched %8.0f (%4.1f%%)  gross %8.0f" % (lo, hi, len(sel), sum(o["q"] for o in sel), 100 * sum(o["q"] for o in sel) / tq, sum(o["gross"] for o in sel)))
print("median trades per condition %d, median side switches %d, median span %ds" % (pct([o["n"] for o in out], .5), pct([o["switches"] for o in out], .5), pct([o["span"] for o in out], .5)))
by = collections.defaultdict(lambda: [0, 0.0, 0.0])
for o in out:
    g = by[o["slug"].split("-")[0]]; g[0] += 1; g[1] += o["q"]; g[2] += o["gross"]
print("league | conditions | matched | locked gross | avg pair cost")
for k, g in sorted(by.items(), key=lambda x: -x[1][2])[:12]:
    print("  %-6s %4d %9.0f %8.0f  %.4f" % (k, g[0], g[1], g[2], 1 - g[2] / g[1]))
big = sorted(out, key=lambda o: -o["gross"])[:6]
for o in big: print("  top:", o["slug"], "matched %.0f pc %.4f trades %d switches %d span %ds" % (o["q"], o["pc"], o["n"], o["switches"], o["span"]))
