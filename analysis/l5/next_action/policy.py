#!/usr/bin/env python3
"""L5 next-action policy: conditional tables + nested H1-H5 incremental power.
Read-only. Settlement is used for scoring only, never as a feature."""
from __future__ import annotations
import gzip, json, math, collections, statistics as st, random

D = [json.loads(l) for l in gzip.open("analysis/l5/next_action/decisions.jsonl.gz", "rt")]
# decision points only: there must be a next action inside the window
DEC = [r for r in D if r["next_action"] in ("EXTEND", "COMPLETE")]
print(f"decision rows total {len(D):,}; with a next EXTEND/COMPLETE {len(DEC):,} "
      f"over {len({r['window_id'] for r in DEC}):,} windows")
print(f"next_action distribution (all rows): "
      f"{dict(collections.Counter(r['next_action'] for r in D))}\n")

def rate(rows):
    n = len(rows)
    return (sum(1 for r in rows if r["next_action"] == "EXTEND") / n, n) if n else (None, 0)

def table(name, keyfn, rows=DEC, order=None):
    g = collections.defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None:
            g[k].append(r)
    print(f"--- P(EXTEND next | {name}) ---")
    ks = order or sorted(g)
    for k in ks:
        if k not in g: continue
        p, n = rate(g[k])
        mk = len({r['window_id'] for r in g[k]})
        print(f"   {str(k):>16}  n={n:6,d} windows={mk:5,d}  P(EXTEND)={p:.3f}")
    print()

def qb(v, edges, fmt="{:g}"):
    if v is None: return None
    for i, e in enumerate(edges):
        if v < e: return f"<{fmt.format(e)}" if i == 0 else f"{fmt.format(edges[i-1])}-{fmt.format(e)}"
    return f">={fmt.format(edges[-1])}"

print("=" * 78); print("CONDITIONAL NEXT-ACTION TABLES  (DISCOVERY)"); print("=" * 78 + "\n")
table("first vs subsequent fill", lambda r: "first" if r["is_first_fill"] else "subsequent",
      order=["first", "subsequent"])
table("this action was", lambda r: r["this_action"], order=["OPEN", "EXTEND", "COMPLETE"])
table("imbalance |res|/gross", lambda r: qb(r["imbalance"], [.05, .15, .30, .50, .75], "{:.2f}"),
      order=["<0.05","0.05-0.15","0.15-0.30","0.30-0.50","0.50-0.75",">=0.75"])
table("abs residual (shares)", lambda r: qb(r["abs_residual"], [5, 20, 50, 150, 400]),
      order=["<5","5-20","20-50","50-150","150-400",">=400"])
table("effective matched pair cost", lambda r: qb(r["pair_cost"], [.90,.95,.98,1.00,1.02], "{:.2f}"),
      order=["<0.90","0.90-0.95","0.95-0.98","0.98-1.00","1.00-1.02",">=1.02"])
table("held-side price", lambda r: qb(r["held_px"], [.15,.30,.50,.70,.85], "{:.2f}"),
      order=["<0.15","0.15-0.30","0.30-0.50","0.50-0.70","0.70-0.85",">=0.85"])
table("opposite-side price", lambda r: qb(r["opp_px"], [.15,.30,.50,.70,.85], "{:.2f}"),
      order=["<0.15","0.15-0.30","0.30-0.50","0.50-0.70","0.70-0.85",">=0.85"])
table("same-side price move since its last print",
      lambda r: qb(r["px_move_same_side"], [-.05,-.01,.01,.05], "{:+.2f}"),
      order=["<-0.05","-0.05--0.01","-0.01-+0.01","+0.01-+0.05",">=+0.05"])
table("seconds to settlement", lambda r: qb(r["secs_to_settle"], [60,120,300,600], "{:g}"),
      order=["<60","60-120","120-300","300-600",">=600"])
table("delay since previous action (s)", lambda r: qb(r["dt_since_prev"], [1,5,20,60], "{:g}"),
      order=["<1","1-5","5-20","20-60",">=60"])
table("maker/taker of this fill", lambda r: r["maker_taker"])
table("asset x duration", lambda r: f"{r['asset']}{r['dur']}m")
table("same-side run length so far", lambda r: min(r["this_run"], 8),
      order=list(range(9)))

# ---------------- nested logistic regression, DEV fit / OOS score ----------------
def feats(r, keys):
    v = []
    for k in keys:
        x = r.get(k)
        if isinstance(x, bool): x = 1.0 if x else 0.0
        v.append(0.0 if x is None else float(x))
    return v

H = {
  "H0 base (time + fill index)": ["secs_to_settle", "secs_from_open", "fill_idx"],
  "H1 cheap-side accumulation":  ["px_move_same_side", "px_move_any", "px_vs_own_side_mean", "held_px"],
  "H2 inventory control":        ["imbalance", "abs_residual", "residual_signed", "gross"],
  "H3 pair-cost optimization":   ["pair_cost", "opp_px", "matched_qty"],
  "H5 maker compensation":       ["is_maker", "this_px"],
}
for r in DEC:
    r["is_maker"] = 1.0 if r["maker_taker"] == "maker" else 0.0
    r["y"] = 1.0 if r["next_action"] == "EXTEND" else 0.0

def fit(rows, keys, l2=1.0, iters=400, lr=0.5):
    X = [feats(r, keys) for r in rows]; y = [r["y"] for r in rows]
    m = len(keys)
    mu = [st.mean(c) for c in zip(*X)]
    sd = [max(st.pstdev(c), 1e-9) for c in zip(*X)]
    X = [[(v - mu[j]) / sd[j] for j, v in enumerate(x)] for x in X]
    w = [0.0] * m; b = 0.0
    n = len(X)
    for _ in range(iters):
        gw = [0.0] * m; gb = 0.0
        for x, yy in zip(X, y):
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            pr = 1 / (1 + math.exp(-max(-30, min(30, z))))
            d = pr - yy
            gb += d
            for j in range(m): gw[j] += d * x[j]
        b -= lr * gb / n
        for j in range(m): w[j] -= lr * (gw[j] / n + l2 * w[j] / n)
    return (w, b, mu, sd, keys)

def logloss(model, rows):
    w, b, mu, sd, keys = model
    s = 0.0
    for r in rows:
        x = feats(r, keys)
        x = [(v - mu[j]) / sd[j] for j, v in enumerate(x)]
        z = b + sum(wi * xi for wi, xi in zip(w, x))
        pr = 1 / (1 + math.exp(-max(-30, min(30, z))))
        pr = min(max(pr, 1e-9), 1 - 1e-9)
        s += -(r["y"] * math.log(pr) + (1 - r["y"]) * math.log(1 - pr))
    return s / len(rows)

dev = [r for r in DEC if r["dev"]]; oos = [r for r in DEC if not r["dev"]]
print("=" * 78)
print(f"NESTED INCREMENTAL EXPLANATORY POWER  (DEV n={len(dev):,} fit, OOS n={len(oos):,} score)")
print(f"DEV/OOS boundary 2026-08-29, the registered split")
print("=" * 78)
base = sum(r["y"] for r in DEC) / len(DEC)
ll0 = -(base * math.log(base) + (1 - base) * math.log(1 - base))
print(f"  P(EXTEND) base rate {base:.4f}; intercept-only log-loss {ll0:.4f}\n")
print(f"  {'model':34s} {'DEV ll':>8} {'OOS ll':>8} {'OOS vs base':>12} {'OOS vs H0':>10}")
keys = list(H["H0 base (time + fill index)"])
m0 = fit(dev, keys); ll_h0 = logloss(m0, oos)
print(f"  {'H0 base (time + fill index)':34s} {logloss(m0,dev):8.4f} {ll_h0:8.4f} "
      f"{ll0-ll_h0:+12.4f} {'--':>10}")
for name in ("H1 cheap-side accumulation","H2 inventory control",
             "H3 pair-cost optimization","H5 maker compensation"):
    k2 = keys + H[name]
    m = fit(dev, k2); lo = logloss(m, oos)
    print(f"  {('H0 + '+name):34s} {logloss(m,dev):8.4f} {lo:8.4f} {ll0-lo:+12.4f} {ll_h0-lo:+10.4f}")
allk = keys + H["H1 cheap-side accumulation"] + H["H2 inventory control"] + \
       H["H3 pair-cost optimization"] + H["H5 maker compensation"]
mA = fit(dev, allk); llA = logloss(mA, oos)
print(f"  {'H0 + H1 + H2 + H3 + H5 (all)':34s} {logloss(mA,dev):8.4f} {llA:8.4f} "
      f"{ll0-llA:+12.4f} {ll_h0-llA:+10.4f}")
print("\n  standardized coefficients of the full model (DEV fit), largest |w| first:")
w, b, mu, sd, kk = mA
for k, wi in sorted(zip(kk, w), key=lambda t: -abs(t[1]))[:14]:
    print(f"    {k:26s} {wi:+.4f}")

# ---------------- H4: does the EXTEND decision carry settlement information? ----------------
print("\n" + "=" * 78)
print("H4 DIRECTIONAL PREDICTION -- does choosing EXTEND predict being RIGHT?")
print("=" * 78)
def edge(rows):
    e = []
    for r in rows:
        nq, npx = r["next_qty"], r["next_px"]
        if nq is None or npx is None: continue
        # the next action's side: EXTEND = held side, COMPLETE = opposite
        side_up = r["held_side_up"] if r["next_action"] == "EXTEND" else (not r["held_side_up"])
        won = 1.0 if (r["settlement_up"] == side_up) else 0.0
        e.append(won - npx)
    return e
for lab, rows in (("next = EXTEND", [r for r in DEC if r["next_action"] == "EXTEND"]),
                  ("next = COMPLETE", [r for r in DEC if r["next_action"] == "COMPLETE"])):
    e = edge(rows)
    # cluster bootstrap over windows
    byw = collections.defaultdict(list)
    for r, ev in zip(rows, e): byw[r["window_id"]].append(ev)
    ws = list(byw)
    random.seed(7); bs = []
    for _ in range(300):
        s = [random.choice(ws) for _ in ws]
        vals = [v for x in s for v in byw[x]]
        bs.append(st.mean(vals))
    bs.sort()
    print(f"  {lab:16s} n={len(e):6,d}  mean edge E[won]-price = {st.mean(e):+.4f}  "
          f"window-clustered 95% CI [{bs[7]:+.4f}, {bs[292]:+.4f}]")
print("  Edge is E[won]-price. The venue price IS the market's probability, so a")
print("  positive edge is required for H4 to have any content.")
