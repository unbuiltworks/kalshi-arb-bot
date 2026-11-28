"""Step 4. Markout of the trader's fills. Arithmetic on the files written by steps 1-3. usage: analyse.py > tables.txt
HISTORICAL observation of another trader on another venue - not our P&L.

Definitions (all per share, for a BUY of token X at price p at time t; positive = price moved the trader's way)
  markout(h)      = price of X at t+h  -  p
  tape price      = a public trade print of the same condition. A print in the other token Y at q is the same trade seen
                    from the other side, so it is used as X at 1-q. 'ask-side' print = a taker bought X (or sold Y);
                    'bid-side' print = a taker sold X (or bought Y).
  usable tape     = last print with  t < print time <= t+h  (so its age is < h). No print in that window -> NOT usable
                    (the last-print value would only be the fill itself or older); those are counted in 'stale' only.
  same-side tape  = last print in the window on the SAME side as the fill (maker buy = bid-side; taker buy = ask-side).
                    Compares like with like, so it does not contain the bid/ask bounce.
  history price   = last /prices-history point with t < point time <= t+h and age <= 90 s (1-minute series).
  settled result  = final outcome price (0 or 1) - p   (taker rows also shown after the fee actually charged).
CI = 2.5%..97.5% of 2000 bootstrap means, resampling whole GAMES (a game holds all its conditions)."""
import bisect, collections, gzip, json, sys
import numpy as np
from common import *
S = json.load(open(SAMPLE)); M = S["markets"]; F = S["fills"]
rng = np.random.default_rng(11)
SPORT = {"tennis": {"itf", "atp", "wta"}, "basketball": {"nba", "cwbb", "cbb", "wnba", "euroleague"}, "am.football": {"cfb", "nfl"},
         "hockey": {"nhl"}, "esports": {"cs2", "lol", "val", "dota2"}}
def sport(lg):
    for k, v in SPORT.items():
        if lg in v: return k
    return "soccer+other"
tapes, hists = {}, {}
for c in M:
    p = os.path.join(TAPE_DIR, c + ".json.gz")
    if not os.path.exists(p): continue
    d = json.load(gzip.open(p, "rt")); rows = d["rows"][::-1]                # oldest first, stable
    ser = []
    for X in (0, 1):
        ts = [r[0] for r in rows]; px = [r[2] if r[1] == X else 1 - r[2] for r in rows]; di = [r[3] if r[1] == X else -r[3] for r in rows]
        ser.append((ts, px, di))
    tapes[c] = {"d": d, "ser": ser, "tx": collections.defaultdict(list)}
    for i, r in enumerate(rows): tapes[c]["tx"][r[5]].append(i)
    for X, t in enumerate(M[c]["tokens"]):
        hp = os.path.join(HIST_DIR, t + ".json.gz")
        if os.path.exists(hp): hists[(c, X)] = json.load(gzip.open(hp, "rt"))
match = collections.Counter(); bracket = collections.Counter()
for f in F:
    c, X, t, p = f["c"], f["tok"], f["ts"], f["px"]; m = M[c]; T = tapes.get(c)
    f["game"] = m["game"]; f["league"] = m["league"]; f["sport"] = sport(m["league"])
    f["ingame"] = None if m["gst"] is None else t >= m["gst"]
    f["settle"] = m["final"][X] - p; f["fee"] = (f["usd"] / f["sh"] - p) if f["taker"] else 0.0
    fd = 1 if f["taker"] else -1
    f["tape_ok"] = bool(T and (T["d"]["complete"] or (T["d"]["covers_from"] or 9e18) <= t))
    f["mo"] = {}
    if T:
        ts, px, di = T["ser"][X]
        idx = T["tx"].get(f["tx"], [])
        good = [i for i in idx if abs(px[i] - p) < 0.0015]
        match["maker" if not f["taker"] else "taker", "found" if good else "tx found, price differs" if idx else "not on tape"] += 1
        if good: match["maker" if not f["taker"] else "taker", "side as expected" if di[good[0]] == fd else "side NOT as expected"] += 1
        lo = bisect.bisect_right(ts, t)                                         # first print strictly after the fill second
        for h in HORIZONS:
            hi = bisect.bisect_right(ts, t + h); o = {}
            if f["tape_ok"]:
                o["n_prints"] = hi - lo
                if hi > lo:
                    o["any"] = px[hi - 1] - p; o["age"] = t + h - ts[hi - 1]
                    for k in range(hi - 1, lo - 1, -1):
                        if di[k] == fd and "same" not in o: o["same"] = px[k] - p
                        if di[k] == -fd and "opp" not in o: o["opp"] = px[k] - p
                        if "same" in o and "opp" in o: break
                    if "same" in o and "opp" in o: o["mid"] = (o["same"] + o["opp"]) / 2
                if hi > 0: o["stale"] = px[hi - 1] - p; o["stale_age"] = t + h - ts[hi - 1]
            f["mo"][h] = o
    H = hists.get((c, X))
    if H:
        ht = [x[0] for x in H]
        k = bisect.bisect_right(ht, t) - 1
        if k >= 0 and t - ht[k] <= 90: f["pre"] = H[k][1] - p                    # history price just before the fill, minus fill price
        for h in HORIZONS:
            k = bisect.bisect_right(ht, t + h) - 1
            if k >= 0 and ht[k] > t and t + h - ht[k] <= 90:
                f["mo"].setdefault(h, {})["hist"] = H[k][1] - p
                o = f["mo"][h]                                                  # is the history price between bid- and ask-side prints?
                if h == 300 and "same" in o and "opp" in o:
                    b, a = (o["same"], o["opp"]) if fd == -1 else (o["opp"], o["same"])
                    bracket["between bid-side and ask-side print" if b - 0.0051 <= o["hist"] <= a + 0.0051 else "outside"] += 1
def stat(fl, key, h, denom=None):
    xs = [(f["mo"].get(h, {}).get(key) if h else f.get(key), f) for f in fl]
    xs = [(v, f) for v, f in xs if v is not None]
    n = len(xs); N = denom if denom is not None else len(fl)
    if n == 0: return {"n": 0, "N": N, "cov": 0.0}
    v = np.array([x for x, _ in xs]) * 100; pr = np.array([f["px"] for _, f in xs]); sh = np.array([f["sh"] for _, f in xs])
    g = {}; gi = np.array([g.setdefault(f["game"], len(g)) for _, f in xs]); G = len(g)
    cs = np.bincount(gi, v, G); cn = np.bincount(gi, minlength=G).astype(float)
    pick = rng.integers(0, G, (2000, G)); bm = cs[pick].sum(1) / cn[pick].sum(1)
    return {"n": n, "N": N, "cov": round(100 * n / N, 1) if N else 0.0, "games": G, "mean_c": round(float(v.mean()), 3), "median_c": round(float(np.median(v)), 3),
            "ci_lo": round(float(np.percentile(bm, 2.5)), 3), "ci_hi": round(float(np.percentile(bm, 97.5)), 3),
            "mean_pct": round(float((v / pr).mean()), 3), "median_pct": round(float(np.median(v / pr)), 3),
            "shw_mean_c": round(float((v * sh).sum() / sh.sum()), 3), "pos": round(float((v > 1e-9).mean() * 100), 1), "neg": round(float((v < -1e-9).mean() * 100), 1)}
def line(lbl, s):
    if not s.get("n"): return "| %s | 0 | %s | | | | | | |" % (lbl, s.get("cov", 0))
    w = " SMALL-N" if s["n"] < 50 or s["games"] < 15 else ""
    return "| %s | %d | %.0f%% | %d | %+.2f | [%+.2f, %+.2f] | %+.2f | %+.2f%% | %+.2f |%s" % (lbl, s["n"], s["cov"], s["games"], s["mean_c"], s["ci_lo"], s["ci_hi"], s["median_c"], s["mean_pct"], s["shw_mean_c"], w)
HEAD = "| cell | n | coverage | games | mean c/share | 95% CI (mean) | median c | mean % of price | share-weighted mean c |\n|---|---|---|---|---|---|---|---|---|"
R = {"label": "HISTORICAL observation of another trader on another venue; not our P&L", "params": S["params"]}
mk = [f for f in F if not f["taker"]]; tk = [f for f in F if f["taker"]]
print("sample: fills %d (maker %d, taker %d), conditions %d, games %d; fill time %s .. %s UTC" % (len(F), len(mk), len(tk), len(set(f["c"] for f in F)), len(set(f["game"] for f in F)),
      dt.datetime.fromtimestamp(min(f["ts"] for f in F), dt.timezone.utc).isoformat()[:16], dt.datetime.fromtimestamp(max(f["ts"] for f in F), dt.timezone.utc).isoformat()[:16]))
print("tape covers the fill time: %d of %d fills; conditions with tape %d, tape rows %d, truncated by page cap %d" % (sum(f["tape_ok"] for f in F), len(F), len(tapes),
      sum(len(T["d"]["rows"]) for T in tapes.values()), sum(1 for T in tapes.values() if not T["d"]["complete"])))
print("fill found on the public tape (same tx, same price):", dict(match))
print("5-min history price vs tape prints:", dict(bracket))
R["sample"] = {"fills": len(F), "maker": len(mk), "taker": len(tk), "conditions": len(set(f["c"] for f in F)), "games": len(set(f["game"] for f in F)),
               "tape_ok": sum(f["tape_ok"] for f in F), "match": {"%s|%s" % k: v for k, v in match.items()}, "bracket": dict(bracket)}
print("\n## A. HEADLINE markout by horizon (cents per share)")
R["headline"] = {}
for nm, fl in (("MAKER", mk), ("TAKER", tk)):
    print("\n" + nm + "\n" + HEAD)
    for h in HORIZONS:
        for key, lbl in (("any", "tape, last print"), ("same", "tape, same-side print"), ("mid", "tape, bid/ask-print average"), ("hist", "1-min history")):
            if key == "hist" and h < 30: continue
            s = stat(fl, key, h); R["headline"]["%s|%d|%s" % (nm, h, key)] = s
            print(line("%ds %s" % (h, lbl), s))
print("\nprint age (s) for usable tape values, median / p90, and stale (not usable) share")
for nm, fl in (("MAKER", mk), ("TAKER", tk)):
    for h in HORIZONS:
        ages = [f["mo"][h]["age"] for f in fl if "age" in f["mo"].get(h, {})]; st = stat(fl, "stale", h)
        R["headline"]["%s|%d|stale" % (nm, h)] = st
        print("  %s %4ds: usable n %d, age median %.0f p90 %.0f | no print in window: %d of %d | STALE last-print mean %+.2fc (n %d, includes the fill itself as the last print)" % (
            nm, h, len(ages), np.median(ages) if ages else -1, np.percentile(ages, 90) if ages else -1, sum(1 for f in fl if f["tape_ok"] and "any" not in f["mo"].get(h, {})), len(fl), st.get("mean_c", float("nan")), st.get("n", 0)))
def bands(f):
    p = f["px"]; u = f["usd"]
    return {"timing": "unknown" if f["ingame"] is None else "in game" if f["ingame"] else "before start",
            "price": "<0.20" if p < .2 else "0.20-0.40" if p < .4 else "0.40-0.60" if p < .6 else "0.60-0.80" if p <= .8 else ">0.80",
            "role": f["role"], "sport": f["sport"], "size": "<$20" if u < 20 else "$20-100" if u < 100 else "$100-500" if u < 500 else ">=$500"}
ORDER = {"timing": ["before start", "in game", "unknown"], "price": ["<0.20", "0.20-0.40", "0.40-0.60", "0.60-0.80", ">0.80"], "role": ["OPEN", "EXTEND", "COMPLETE", "ADD_BOTH"],
         "sport": ["tennis", "basketball", "am.football", "soccer+other", "hockey", "esports"], "size": ["<$20", "$20-100", "$100-500", ">=$500"]}
print("\n## B. BREAKDOWNS, 1-min history price, 2 min and 5 min")
R["breakdown"] = {}
for nm, fl in (("MAKER", mk), ("TAKER", tk)):
    for dim in ("timing", "price", "role", "sport", "size"):
        print("\n%s by %s\n" % (nm, dim) + HEAD)
        for b in ORDER[dim]:
            sel = [f for f in fl if bands(f)[dim] == b]
            if not sel: continue
            for h in (120, 300):
                s = stat(sel, "hist", h); R["breakdown"]["%s|%s|%s|%d" % (nm, dim, b, h)] = s
                print(line("%s %dmin (fills %d)" % (b, h // 60, len(sel)), s))
lg = collections.Counter(f["league"] for f in mk)
print("\nMAKER by league (top 10 by fills), 5 min\n" + HEAD)
for l, _ in lg.most_common(10):
    s = stat([f for f in mk if f["league"] == l], "hist", 300); R["breakdown"]["MAKER|league|%s|300" % l] = s; print(line(l, s))
print("\n## C. SETTLEMENT vs 5-min markout (history price). settled = final outcome price - fill price, cents per share")
R["settlement"] = {}
for nm, fl in (("MAKER", mk), ("TAKER", tk), ("ALL", F)):
    s = stat(fl, "settle", 0); R["settlement"][nm + "|all"] = s
    print("\n%s all sampled fills\n" % nm + HEAD); print(line("settled result", s))
    if nm == "TAKER":
        for f in fl: f["settle_net"] = f["settle"] - f["fee"]
        s = stat(fl, "settle_net", 0); R["settlement"][nm + "|net_fee"] = s; print(line("settled result after taker fee", s))
    have = sorted([f for f in fl if "hist" in f["mo"].get(300, {})], key=lambda f: f["mo"][300]["hist"])
    k = len(have) // 3
    for lbl, sel in (("lowest third", have[:k]), ("middle third", have[k:2 * k]), ("highest third", have[2 * k:])):
        a = stat(sel, "hist", 300); b = stat(sel, "settle", 0); R["settlement"]["%s|tercile|%s" % (nm, lbl)] = {"markout5m": a, "settled": b}
        print("| %s of 5-min markout: markout mean %+.2fc (range %+.1f..%+.1f) -> settled mean %+.2fc [%+.2f, %+.2f], median %+.1fc, n %d, games %d |" % (
            lbl, a["mean_c"], sel[0]["mo"][300]["hist"] * 100, sel[-1]["mo"][300]["hist"] * 100, b["mean_c"], b["ci_lo"], b["ci_hi"], b["median_c"], b["n"], b["games"]))
    if len(have) > 10:
        x = np.array([f["mo"][300]["hist"] for f in have]); y = np.array([f["settle"] for f in have])
        R["settlement"][nm + "|corr"] = round(float(np.corrcoef(x, y)[0, 1]), 3)
        print("correlation(5-min markout, settled result) = %.3f; sd of markout %.1fc, sd of settled result %.1fc" % (np.corrcoef(x, y)[0, 1], x.std() * 100, y.std() * 100))
print("\n## D. ADVERSE SELECTION, maker fills")
R["adverse"] = {}
s = stat(mk, "pre", 0); R["adverse"]["pre"] = s
print("history price just before the fill minus fill price (how far below the prevailing price the resting bid was filled):\n" + HEAD); print(line("pre-fill history price - fill", s))
print("\nbid-side prints after a maker fill vs the fill price (same-side: no bid/ask bounce): share lower / equal / higher")
for h in HORIZONS:
    v = np.array([f["mo"][h]["same"] for f in mk if "same" in f["mo"].get(h, {})])
    if len(v):
        d = {"n": int(len(v)), "lower": round(float((v < -1e-9).mean() * 100), 1), "equal": round(float((abs(v) <= 1e-9).mean() * 100), 1), "higher": round(float((v > 1e-9).mean() * 100), 1), "mean_c": round(float(v.mean() * 100), 3)}
        R["adverse"]["same|%d" % h] = d; print("  %4ds n %d: lower %.1f%% equal %.1f%% higher %.1f%% mean %+.2fc" % (h, d["n"], d["lower"], d["equal"], d["higher"], d["mean_c"]))
print("\nbalanced panel: maker fills with a usable history price before the fill AND at 2, 5 and 15 min")
pan = [f for f in mk if "pre" in f and all("hist" in f["mo"].get(h, {}) for h in (120, 300, 900))]
print(HEAD)
s = stat(pan, "pre", 0, len(mk)); R["adverse"]["panel|pre"] = s; print(line("edge at fill: pre-fill price - fill", s))
for h in (120, 300, 900):
    s = stat(pan, "hist", h, len(mk)); R["adverse"]["panel|%d" % h] = s; print(line("markout %d min: price - fill" % (h // 60), s))
    for f in pan: f["drift%d" % h] = f["mo"][h]["hist"] - f["pre"]
    s = stat(pan, "drift%d" % h, 0, len(mk)); R["adverse"]["panel|drift|%d" % h] = s; print(line("price move %d min: price - pre-fill price" % (h // 60), s))
print("\nsame for TAKER fills (pre-fill price - fill is negative when the taker paid above the prevailing price)")
pan = [f for f in tk if "pre" in f and all("hist" in f["mo"].get(h, {}) for h in (120, 300, 900))]
print(HEAD); s = stat(pan, "pre", 0, len(tk)); R["adverse"]["taker_panel|pre"] = s; print(line("pre-fill price - fill", s))
for h in (120, 300, 900):
    s = stat(pan, "hist", h, len(tk)); R["adverse"]["taker_panel|%d" % h] = s; print(line("markout %d min" % (h // 60), s))
    for f in pan: f["drift%d" % h] = f["mo"][h]["hist"] - f["pre"]
    s = stat(pan, "drift%d" % h, 0, len(tk)); R["adverse"]["taker_panel|drift|%d" % h] = s; print(line("price move %d min: price - pre-fill price" % (h // 60), s))
print("\n## E. DISTRIBUTION of markout (history price), cents per share: p5 / p25 / p50 / p75 / p95, share <= -5c, share >= +5c, mean with the 1% tails clipped")
R["distribution"] = {}
for nm, fl in (("MAKER", mk), ("TAKER", tk)):
    for h in (120, 300, 900):
        v = np.array([f["mo"][h]["hist"] for f in fl if "hist" in f["mo"].get(h, {})]) * 100
        q = [round(float(x), 2) for x in np.percentile(v, (5, 25, 50, 75, 95))]; lo, hi = np.percentile(v, (1, 99))
        d = {"n": int(len(v)), "pctl": q, "le_m5": round(float((v <= -5).mean() * 100), 1), "ge_p5": round(float((v >= 5).mean() * 100), 1), "clipped_mean": round(float(np.clip(v, lo, hi).mean()), 3)}
        R["distribution"]["%s|%d" % (nm, h)] = d
        print("  %s %2d min n %d: %s | <=-5c %.1f%% | >=+5c %.1f%% | clipped mean %+.2fc" % (nm, h // 60, d["n"], q, d["le_m5"], d["ge_p5"], d["clipped_mean"]))
ex = sum(1 for f in F if f["ts"] + 900 > (M[f["c"]]["closed_ts"] or 9e18)); R["sample"]["fills_within_15min_of_close"] = ex
print("fills within 15 min of the market's close time:", ex)
json.dump(R, open(os.path.join(HERE, "rn1_markout_results.json"), "w"), indent=1)
