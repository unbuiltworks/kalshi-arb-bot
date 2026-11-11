"""Validate pair-cost ceilings on the UNTOUCHED chronological TEST half.

Reports net DOLLARS, opportunity count, unmatched losses and a bootstrap
interval -- not just profit per matched contract, which flatters a rule that
simply trades less.
"""
from __future__ import annotations
import json, random
from decimal import Decimal as D

W = {w["slug"]: w for w in
     (json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl"))}
TR = [W[s] for s in json.load(open("/tmp/claude-0/train.json")) if s in W]
TE = [W[s] for s in json.load(open("/tmp/claude-0/test.json")) if s in W]

def dd(w, k): return D(w.get(k) or 0)

def evaluate(rows, ceiling):
    """A ceiling REFUSES pair creation above it. Refused windows keep no
    matched book -- but the first leg still filled, so the residual they
    were already carrying is NOT avoided. That is the honest accounting:
    refusing to complete does not un-buy the leg."""
    kept = [w for w in rows if w.get("pair_cost")
            and D(w["pair_cost"]) <= ceiling and D(w["matched"]) > 0]
    mat = sum((dd(w, "matched_pnl") for w in kept), D(0))
    res = sum((dd(w, "residual_pnl") for w in kept), D(0))
    mq = sum((dd(w, "matched") for w in kept), D(0))
    rq = sum((dd(w, "residual") for w in kept), D(0))
    return {"n": len(kept), "net": mat + res, "matched_pnl": mat,
            "residual_pnl": res, "mq": mq, "rq": rq,
            "per_ctr": (mat + res) / mq if mq else D(0)}

def boot(rows, ceiling, n=400):
    kept = [w for w in rows if w.get("pair_cost")
            and D(w["pair_cost"]) <= ceiling and D(w["matched"]) > 0]
    if not kept: return (0.0, 0.0)
    rnd = random.Random(12345); out = []
    for _ in range(n):
        s = [kept[rnd.randrange(len(kept))] for _ in range(len(kept))]
        out.append(float(sum((dd(w, "matched_pnl") + dd(w, "residual_pnl")
                              for w in s), D(0))))
    out.sort()
    return (out[int(0.05*n)], out[int(0.95*n)])

CEIL = [D("0.96"), D("0.97"), D("0.98"), D("0.99"), D("1.00"), D("9")]
for name, rows in (("TRAIN (used to form the hypothesis)", TR),
                   ("TEST  (untouched, out of sample)", TE)):
    print("\n=== %s  n=%d windows ===" % (name, len(rows)))
    print("%-10s %6s %12s %12s %12s %10s %24s" % (
        "ceiling", "opps", "net_$", "matched_$", "unmatched_$", "net/ctr",
        "net 90% bootstrap"))
    for c in CEIL:
        r = evaluate(rows, c)
        lo, hi = boot(rows, c)
        lbl = ("<=%.2f" % c) if c < 9 else "none"
        print("%-10s %6d %+12.2f %+12.2f %+12.2f %+10.5f  [%+9.2f, %+9.2f]"
              % (lbl, r["n"], r["net"], r["matched_pnl"], r["residual_pnl"],
                 r["per_ctr"], lo, hi))

print("\n  opps        = windows whose pair would be CREATED under the ceiling")
print("  unmatched_$ = residual P&L of those windows. A ceiling does NOT")
print("                avoid residual: the first leg already filled.")
print("  net/ctr flatters a restrictive rule; net_$ is what pays.")
