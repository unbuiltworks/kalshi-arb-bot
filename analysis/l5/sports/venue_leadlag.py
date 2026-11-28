"""Read-only: which venue moves first? From venue_sampler.py rows.

For each market: every time ONE venue's mid moves by at least MOVE (default
1c) between two samples at most GAP s apart, look at what the OTHER venue's
mid did (a) in the 30 s BEFORE that move and (b) in the 30 s AFTER it.
If the other venue had already moved the same way, it LED; if it follows
afterwards, it LAGS. Counts only; no interpolation: a comparison needs a
sample of the other venue within TOL s of each reference time.

usage: venue_leadlag.py <rows.jsonl> [move=0.01]
"""
import bisect, json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
MOVE = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
GAP, WIN, TOL = 12.0, 30.0, 8.0
by = collections.defaultdict(list)
for r in rows:
    by[r["slug"]].append(r)
def series(rs, b, a):
    out = [(r["t"], (r[b] + r[a]) / 2) for r in rs
           if r.get(b) is not None and r.get(a) is not None and r[a] > r[b]]
    return [t for t, _ in out], [m for _, m in out]
def at(ts, ms, t):
    i = bisect.bisect_left(ts, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(ts) and abs(ts[j] - t) <= TOL:
            if best is None or abs(ts[j] - t) < abs(ts[best] - t):
                best = j
    return None if best is None else ms[best]
tot = collections.Counter()
for slug, rs in sorted(by.items()):
    rs.sort(key=lambda r: r["t"])
    V = {"pmus": series(rs, "ub", "ua"), "intl": series(rs, "ib", "ia")}
    c = collections.Counter()
    for me, other in (("pmus", "intl"), ("intl", "pmus")):
        ts, ms = V[me]
        ots, oms = V[other]
        for i in range(1, len(ts)):
            d = ms[i] - ms[i - 1]
            if abs(d) < MOVE - 1e-9 or ts[i] - ts[i - 1] > GAP:
                continue
            o_before, o_at, o_after = (at(ots, oms, ts[i - 1] - WIN),
                                       at(ots, oms, ts[i - 1]),
                                       at(ots, oms, ts[i] + WIN))
            if None in (o_before, o_at, o_after):
                c[me + "_move_no_comparison"] += 1
                continue
            sgn = 1 if d > 0 else -1
            led = sgn * (o_at - o_before) >= MOVE / 2 - 1e-9
            fol = sgn * (o_after - o_at) >= MOVE / 2 - 1e-9
            c[me + "_moves"] += 1
            c["%s_moved_first_before_%s" % (other, me)] += led
            c["%s_followed_%s" % (other, me)] += fol
    span = (rs[-1]["t"] - rs[0]["t"]) / 60 if rs else 0
    print(slug, "| %.0f min, %d rows" % (span, len(rs)), dict(c))
    tot.update(c)
print("TOTAL", dict(tot))
