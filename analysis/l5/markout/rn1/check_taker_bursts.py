"""Side check: how much of the taker markout could be the trader's own follow-on taker buying?
For each taker fill: is there another taker fill by the trader in the same token within N seconds AFTER it?
Then the 2-min / 5-min history markout for taker fills with NO own taker fill in the same token in the next 5 minutes."""
import bisect, collections, gzip, json
import numpy as np
from common import *
S = json.load(open(SAMPLE)); M = S["markets"]
tk = [f for f in S["fills"] if f["taker"]]
by = collections.defaultdict(list)
for f in tk: by[(f["c"], f["tok"])].append(f["ts"])
for v in by.values(): v.sort()
def nxt(f):
    v = by[(f["c"], f["tok"])]; k = bisect.bisect_right(v, f["ts"])
    return v[k] - f["ts"] if k < len(v) else 9e18
for N in (5, 30, 120, 300): print("taker fills followed by another own taker fill in the same token within %ds: %.0f%%" % (N, 100 * sum(nxt(f) <= N for f in tk) / len(tk)))
lone = [f for f in tk if nxt(f) > 300]
rng = np.random.default_rng(3); out = {}
for h in (120, 300):
    vals = []
    for f in lone:
        p = os.path.join(HIST_DIR, M[f["c"]]["tokens"][f["tok"]] + ".json.gz")
        H = json.load(gzip.open(p, "rt")); ht = [x[0] for x in H]; k = bisect.bisect_right(ht, f["ts"] + h) - 1
        if k >= 0 and ht[k] > f["ts"] and f["ts"] + h - ht[k] <= 90: vals.append((M[f["c"]]["game"], (H[k][1] - f["px"]) * 100))
    g = {}; gi = np.array([g.setdefault(a, len(g)) for a, _ in vals]); v = np.array([b for _, b in vals]); G = len(g)
    cs = np.bincount(gi, v, G); cn = np.bincount(gi, minlength=G).astype(float); pick = rng.integers(0, G, (2000, G)); bm = cs[pick].sum(1) / cn[pick].sum(1)
    out[h] = {"n": len(v), "of": len(lone), "games": G, "mean_c": round(float(v.mean()), 2), "median_c": round(float(np.median(v)), 2), "ci": [round(float(np.percentile(bm, 2.5)), 2), round(float(np.percentile(bm, 97.5)), 2)]}
    print("taker fills with no own taker fill in that token in the next 5 min, %d-min history markout:" % (h // 60), out[h])
json.dump(out, open(os.path.join(HERE, "check_taker_bursts.json"), "w"))
