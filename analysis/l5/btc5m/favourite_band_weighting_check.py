"""Check of the favourite-band result's weighting.

The hold-out table averaged each window's MEAN return per signal, so a
window with one signal counted as much as a window with twenty-five. A
strategy that buys at every signal earns the SUM. Both are shown, with
the same signals, prices, fee and outcome; intervals bootstrap windows.
"""
import collections, datetime as dt, gzip, json, random, sys
K = 0.095
q = collections.defaultdict(set); tb = {}
for line in gzip.open(sys.argv[1], "rt"):
    r = json.loads(line)
    if r.get("to_beat") is not None:
        tb.setdefault(r["slug"], float(r["to_beat"]))
    if r.get("closed") or r.get("bid") is None or r.get("ask") is None:
        continue
    q[r["slug"]].add((float(r["t"]), float(r["bid"]), float(r["ask"])))
W = collections.defaultdict(lambda: collections.defaultdict(list))
for s, rows in q.items():
    d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") + dt.timedelta(minutes=5)
    nx = "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")
    if s not in tb or nx not in tb:
        continue
    res = "UP" if tb[nx] >= tb[s] else "DOWN"
    per = "hold-out 11-11..16" if s[-16:-6] < "2026-11-17" else "found on 11-17..20"
    rows = sorted(rows)
    for i, (t, b, a) in enumerate(rows[:-1]):
        if not (10 <= t <= 280):
            continue
        _t, nb, na = rows[i + 1]
        for oc, now, px in (("UP", a, na), ("DOWN", 1 - b, 1 - nb)):
            if .5 <= now < .7 and 0.01 < px < 0.995:
                w = 1.0 if res == oc else 0.0
                for k in (per, "all ten days"):
                    W[k][s].append((w - px, w - px - K * px * (1 - px), w))


def ratio(ws, j, n=3000):
    tot = lambda xs: sum(sum(v[j] for v in x) for x in xs) / sum(len(x) for x in xs)
    rng = random.Random(2); k = len(ws)
    ms = sorted(tot([rng.choice(ws) for _ in range(k)]) for _ in range(n))
    return "%+.4f [%+.3f,%+.3f]" % (tot(ws), ms[int(.025 * n)], ms[int(.975 * n)])


def wmean(ws, j, n=3000):
    v = [sum(x[j] for x in w) / len(w) for w in ws]
    rng = random.Random(2); k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return "%+.4f [%+.3f,%+.3f]" % (sum(v) / k, ms[int(.025 * n)], ms[int(.975 * n)])


for k in ("found on 11-17..20", "hold-out 11-11..16", "all ten days"):
    ws = list(W[k].values())
    n = sum(len(w) for w in ws)
    win = [w for w in ws if w[0][2] == 1.0 or any(x[2] for x in w)]
    print("\n%s: %d windows, %d signals" % (k, len(ws), n))
    print("   each WINDOW counted once (as reported): before fee %s   after fee %s" % (wmean(ws, 0), wmean(ws, 1)))
    print("   each SIGNAL counted once (what buying every signal earns per share): before fee %s   after fee %s" % (ratio(ws, 0), ratio(ws, 1)))
    cnt_w = [len(w) for w in ws if sum(x[2] for x in w) > len(w) / 2]
    cnt_l = [len(w) for w in ws if sum(x[2] for x in w) <= len(w) / 2]
    print("   signals per window when the signalled side mostly WON: %.1f   mostly LOST: %.1f" % (sum(cnt_w) / max(len(cnt_w), 1), sum(cnt_l) / max(len(cnt_l), 1)))
