"""Candidate rule: where should the PAIR-COST line sit?

L5's whole matched edge is the pair discount, so this buckets the reconciled
windows by realised pair cost and measures net per matched contract in each.
It is the one rule that needs no limit raised: it only ever REFUSES.
"""
from __future__ import annotations
import json, collections
from decimal import Decimal as D

W = [json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl")]
S = [w for w in W if w.get("has_redeem") and w.get("winner")]
def ok(w):
    h = D(w["up"]) if w["winner"] == "Up" else D(w["dn"])
    return h > 0 and abs(D(w["redeemed"])/h - D(1)) <= D("0.01")
R = [w for w in S if ok(w) and w.get("pair_cost") and D(w["matched"]) > 0]
print("reconciled windows with a two-sided pair: %d\n" % len(R))

EDGES = [D("0"), D("0.90"), D("0.94"), D("0.96"), D("0.967"), D("0.98"),
         D("1.00"), D("1.02"), D("9")]
print("%-16s %6s %11s %10s %10s %10s %8s" % (
    "pair cost", "n", "matched_q", "mat/ctr", "res/ctr", "net/ctr", "res_shr"))
for lo, hi in zip(EDGES, EDGES[1:]):
    rows = [w for w in R if lo <= D(w["pair_cost"]) < hi]
    if not rows:
        continue
    mat = sum((D(w.get("matched_pnl") or 0) for w in rows), D(0))
    res = sum((D(w.get("residual_pnl") or 0) for w in rows), D(0))
    mq = sum((D(w["matched"]) for w in rows), D(0))
    rq = sum((D(w["residual"]) for w in rows), D(0))
    if mq <= 0:
        continue
    lbl = "%.3f-%.3f" % (lo, hi) if hi < 9 else ">=%.2f" % lo
    print("%-16s %6d %11.0f %+10.5f %+10.5f %+10.5f %7.1f%%" % (
        lbl, len(rows), mq, mat/mq, (res/rq if rq else 0), (mat+res)/mq,
        100.0*float(rq)/float(mq)))

print("\n\nCUMULATIVE: what a CEILING at each level would have captured")
print("%-12s %6s %11s %12s %12s %10s" % (
    "ceiling", "n", "matched_q", "total_net", "net/ctr", "kept_%"))
allq = sum((D(w["matched"]) for w in R), D(0))
for c in (D("0.94"), D("0.96"), D("0.967"), D("0.98"), D("1.00"),
          D("1.02"), D("9")):
    rows = [w for w in R if D(w["pair_cost"]) <= c]
    if not rows:
        continue
    mat = sum((D(w.get("matched_pnl") or 0) for w in rows), D(0))
    res = sum((D(w.get("residual_pnl") or 0) for w in rows), D(0))
    mq = sum((D(w["matched"]) for w in rows), D(0))
    print("%-12s %6d %11.0f %+12.2f %+12.5f %9.1f%%" % (
        ("<=%.3f" % c) if c < 9 else "none", len(rows), mq, mat+res,
        (mat+res)/mq if mq else 0, 100.0*float(mq)/float(allq)))

print("\n  mat/ctr and net/ctr are per MATCHED contract.")
print("  A ceiling only refuses; it never needs a limit raised.")
print("  net/ctr is what a dollar of matched inventory actually earns.")
