"""Where inside the favourite band (0.50-0.70) does the taker edge sit?
Per signal, bought at the next ~10 s sample's ask, after our taker fee.
Rows: ask at the signal. Columns: seconds into the window.
Each cell: found-on days 11-17..20 / hold-out days 11-11..16 (windows).
* = 95% bootstrap interval over windows excludes zero.
"""
import collections, datetime as dt, gzip, json, random, sys
K = 0.095
PB = ((.50, .55), (.55, .60), (.60, .65), (.65, .70), (.70, .85))
TB = ((10, 60), (60, 120), (120, 180), (180, 240), (240, 281), (10, 281))



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


def cell(v):
    if len(v) < 20:
        return "      -      "
    pt, (lo, hi), k = _pool(v, n=1500, seed=1)
    return "%+.3f%s(%4d)" % (pt, "*" if lo > 0 or hi < 0 else " ", k)


q = collections.defaultdict(set); tb = {}
for line in gzip.open(sys.argv[1], "rt"):
    r = json.loads(line)
    if r.get("to_beat") is not None:
        tb.setdefault(r["slug"], float(r["to_beat"]))
    if r.get("closed") or r.get("bid") is None or r.get("ask") is None:
        continue
    q[r["slug"]].add((float(r["t"]), float(r["bid"]), float(r["ask"])))
acc = collections.defaultdict(lambda: collections.defaultdict(list))
for s, rows in q.items():
    d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") + dt.timedelta(minutes=5)
    nx = "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")
    if s not in tb or nx not in tb:
        continue
    res = "UP" if tb[nx] >= tb[s] else "DOWN"
    per = "H" if s[-16:-6] < "2026-11-17" else "F"
    rows = sorted(rows)
    for i, (t, b, a) in enumerate(rows[:-1]):
        _t, nb, na = rows[i + 1]
        for oc, now, px in (("UP", a, na), ("DOWN", 1 - b, 1 - nb)):
            if not (0.01 < px < 0.995):
                continue
            v = (1.0 if res == oc else 0.0) - px - K * px * (1 - px)
            for pb in PB:
                if pb[0] <= now < pb[1]:
                    for tbk in TB:
                        if tbk[0] <= t < tbk[1]:
                            acc[(per, pb, tbk)][s].append(v)
print("%-10s" % "ask" + "".join("%-29s" % ("%d-%ds" % (a, b - (b == 281))) for a, b in TB))
for pb in PB:
    line = "%.2f-%.2f " % pb
    for tbk in TB:
        f = [(sum(x), len(x)) for x in acc[("F", pb, tbk)].values()]
        h = [(sum(x), len(x)) for x in acc[("H", pb, tbk)].values()]
        line += "%s/%s  " % (cell(f), cell(h))
    print(line)
