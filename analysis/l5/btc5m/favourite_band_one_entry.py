"""The live-test shape of the favourite-band taker rule: ONE buy per
window. First ~10 s sample (10-280 s) where a side's ask is 0.50-0.70;
buy one share at the next sample's ask; fee 0.095 x p x (1 - p); hold to
settlement. USD per window traded, 95% bootstrap over windows.
Also split by when in the window the signal first appears.
"""
import collections, datetime as dt, gzip, json, math, random, sys
K = 0.095


def boot(v, n=4000, seed=3):
    rng = random.Random(seed); k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


q = collections.defaultdict(set); tb = {}
for line in gzip.open(sys.argv[1], "rt"):
    r = json.loads(line)
    if r.get("to_beat") is not None:
        tb.setdefault(r["slug"], float(r["to_beat"]))
    if r.get("closed") or r.get("bid") is None or r.get("ask") is None:
        continue
    q[r["slug"]].add((float(r["t"]), float(r["bid"]), float(r["ask"])))
out = collections.defaultdict(list)
for s, rows in q.items():
    d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") + dt.timedelta(minutes=5)
    nx = "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")
    if s not in tb or nx not in tb:
        continue
    res = "UP" if tb[nx] >= tb[s] else "DOWN"
    rows = sorted(rows)
    for i, (t, b, a) in enumerate(rows[:-1]):
        if not (10 <= t <= 280):
            continue
        pick = "UP" if .5 <= a < .7 else "DOWN" if .5 <= 1 - b < .7 else None
        if pick is None:
            continue
        _t, nb, na = rows[i + 1]
        px = na if pick == "UP" else 1 - nb
        if not (0.01 < px < 0.995):
            break
        v = (1.0 if res == pick else 0.0) - px - K * px * (1 - px)
        per = "hold-out 11-11..16" if s[-16:-6] < "2026-11-17" else "found on 11-17..20"
        tt = "first 60 s" if t < 60 else "60-180 s" if t < 180 else "180-280 s"
        for key in ((per, "any time"), ("all ten days", "any time"),
                    ("all ten days", tt)):
            out[key].append((v, px))
        break
for key in sorted(out):
    v = [x for x, _ in out[key]]
    lo, hi = boot(v); m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
    print("%-20s %-11s windows %4d  mean price %.3f  net per window %+.4f [%+.3f,%+.3f]  sd %.2f" % (
        key[0], key[1], len(v), sum(p for _, p in out[key]) / len(v), m, lo, hi, sd))
