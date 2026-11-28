"""Does more frequency improve NET profitability, or only exposure?

Segments the reconciled windows by transaction count and measures, in each
bucket, the three quantities that decide it: the matched edge per contract,
the residual drag per matched contract, and the net of the two. If frequency
only bought exposure, net per matched contract would fall as the count rises.
"""
from __future__ import annotations
import json, collections
from decimal import Decimal as D

W = [json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl")]
S = [w for w in W if w.get("has_redeem") and w.get("winner")]
def ok(w):
    hold = D(w["up"]) if w["winner"] == "Up" else D(w["dn"])
    return hold > 0 and abs(D(w["redeemed"])/hold - D(1)) <= D("0.01")
R = [w for w in S if ok(w)]

def bucket(n):
    for lo, hi in ((1, 20), (21, 50), (51, 100), (101, 150), (151, 250),
                   (251, 10**9)):
        if lo <= n <= hi:
            return "%d-%s" % (lo, "+" if hi > 10**8 else hi)
    return "?"

B = collections.defaultdict(list)
for w in R:
    B[bucket(w["n_trades"])].append(w)

order = ["1-20", "21-50", "51-100", "101-150", "151-250", "251-+"]
print("NET PER MATCHED CONTRACT, by transactions per window")
print("(reconciled windows only, n=%d)\n" % len(R))
print("%-9s %6s %10s %10s %10s %10s %9s %8s" % (
    "trades", "n", "matched_q", "mat/ctr", "res/ctr", "net/ctr",
    "pair_cost", "res_shr"))
for k in order:
    rows = B.get(k) or []
    if not rows:
        continue
    mat = sum((D(w.get("matched_pnl") or 0) for w in rows), D(0))
    res = sum((D(w.get("residual_pnl") or 0) for w in rows), D(0))
    mq = sum((D(w["matched"]) for w in rows), D(0))
    rq = sum((D(w["residual"]) for w in rows), D(0))
    pcs = [float(D(w["pair_cost"])) for w in rows if w.get("pair_cost")]
    pcs.sort()
    if mq <= 0:
        continue
    print("%-9s %6d %10.0f %+10.5f %+10.5f %+10.5f %9.4f %7.1f%%" % (
        k, len(rows), mq, mat/mq, (res/rq if rq else 0), (mat+res)/mq,
        (pcs[len(pcs)//2] if pcs else 0), 100.0*float(rq)/float(mq)))

print("\n   mat/ctr = matched P&L per MATCHED contract (the edge)")
print("   res/ctr = residual P&L per RESIDUAL contract (the drag)")
print("   net/ctr = (matched+residual) per MATCHED contract  <- what matters")
print("   res_shr = residual contracts as %% of matched (exposure taken on)")

print("\n\nIMBALANCE DIRECTION: does L5 trade to reduce or to extend?")
rf = sorted(w["reduce_frac"] for w in R)
print("   fraction of trades that REDUCE imbalance:")
print("      p10=%.3f median=%.3f p90=%.3f"
      % (rf[len(rf)//10], rf[len(rf)//2], rf[len(rf)*9//10]))
print("   -> a median of %.1f%% of L5's trades reduce imbalance, so ~%.1f%%"
      % (100*rf[len(rf)//2], 100*(1-rf[len(rf)//2])))
print("      extend it. Both are first-class, as the operating model says.")

print("\n\nPEAK UNMATCHED EXPOSURE vs the matched book it ends with:")
pk = sorted(float(D(w["peak_unmatched"])/D(w["matched"]))
            for w in R if D(w["matched"]) > 0)
print("   peak_unmatched / final_matched: p10=%.3f median=%.3f p90=%.3f"
      % (pk[len(pk)//10], pk[len(pk)//2], pk[len(pk)*9//10]))

print("\n\nTHE DECISIVE TEST: correlation of net/matched-contract with count")
xs, ys = [], []
for w in R:
    if D(w["matched"]) <= 0:
        continue
    xs.append(float(w["n_trades"]))
    ys.append(float((D(w.get("matched_pnl") or 0)
                     + D(w.get("residual_pnl") or 0)) / D(w["matched"])))
n = len(xs)
mx, my = sum(xs)/n, sum(ys)/n
cov = sum((a-mx)*(b-my) for a, b in zip(xs, ys))
vx = sum((a-mx)**2 for a in xs) ** 0.5
vy = sum((b-my)**2 for b in ys) ** 0.5
print("   Pearson r(n_trades, net per matched contract) = %+.4f  (n=%d)"
      % (cov/(vx*vy) if vx and vy else 0, n))
print("   r near 0 means frequency does NOT change per-contract economics:")
print("   it scales the SAME edge, and scales the exposure with it.")
