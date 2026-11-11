"""Audit: reconcile the two baselines, split chronologically, measure bias."""
from __future__ import annotations
import json
from decimal import Decimal as D

W = [json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl")]
for w in W:
    w["ws"] = int(w["slug"].rsplit("-", 1)[1])
S = [w for w in W if w.get("has_redeem") and w.get("winner")]
def ok(w):
    h = D(w["up"]) if w["winner"] == "Up" else D(w["dn"])
    return h > 0 and abs(D(w["redeemed"])/h - D(1)) <= D("0.01")
R = [w for w in S if ok(w)]

print("=== 1. RECONCILING THE TWO BASELINES ===")
A = R
B = [w for w in R if w.get("pair_cost") and D(w["matched"]) > 0]
only = [w for w in R if w not in B]
sa = sum((D(w.get("matched_pnl") or 0) + D(w.get("residual_pnl") or 0)
          for w in A), D(0))
sb = sum((D(w.get("matched_pnl") or 0) + D(w.get("residual_pnl") or 0)
          for w in B), D(0))
print("  all reconciled windows          n=%4d  matched+residual %+10.2f"
      % (len(A), sa))
print("  ...with a two-sided pair        n=%4d  matched+residual %+10.2f"
      % (len(B), sb))
print("  difference                      n=%4d  %+10.2f" % (len(only), sa-sb))
print("  -> the gap is %d ONE-SIDED windows (matched=0, no pair cost), whose"
      % len(only))
print("     P&L is pure directional residual. $17,394 counts them; $16,964")
print("     is the pair-cost table, which cannot bucket a window with no pair.")
rp = sum((D(w.get("residual_pnl") or 0) for w in only), D(0))
mp = sum((D(w.get("matched_pnl") or 0) for w in only), D(0))
print("     those %d windows: residual %+.2f  matched %+.2f" % (len(only), rp, mp))

print("\n=== 2. SELECTION BIAS FROM THE 65.5%% EXCLUDED ===")
X = [w for w in S if not ok(w)]
def prof(rows, name):
    nt = sorted(w["n_trades"] for w in rows)
    pc = sorted(float(D(w["pair_cost"])) for w in rows if w.get("pair_cost"))
    up = sorted(float(D(w["up"]) + D(w["dn"])) for w in rows)
    pk = sorted(float(D(w["peak_unmatched"])) for w in rows)
    print("  %-10s n=%4d | trades med=%4d p90=%4d | gross_q med=%8.1f"
          " | pair med=%s | peak_unm med=%6.1f"
          % (name, len(rows), nt[len(nt)//2], nt[len(nt)*9//10],
             up[len(up)//2],
             ("%.4f" % pc[len(pc)//2]) if pc else "   n/a",
             pk[len(pk)//2]))
prof(R, "INCLUDED")
prof(X, "EXCLUDED")
print("  -> if exclusion tracked trade count, the kept subset would")
print("     under-represent exactly the high-activity windows that carry")
print("     the matched edge. Compare the medians above.")
inc_hi = sum(1 for w in R if w["n_trades"] >= 101)
exc_hi = sum(1 for w in X if w["n_trades"] >= 101)
print("  windows with >=101 trades: included %d/%d (%.1f%%), excluded %d/%d (%.1f%%)"
      % (inc_hi, len(R), 100.0*inc_hi/len(R),
         exc_hi, len(X), 100.0*exc_hi/len(X)))

print("\n=== 3. CHRONOLOGICAL TRAIN/TEST SPLIT (no shuffling) ===")
R.sort(key=lambda w: w["ws"])
cut = len(R) // 2
TR, TE = R[:cut], R[cut:]
import datetime as dt
def rng(rows):
    a = dt.datetime.fromtimestamp(rows[0]["ws"], dt.timezone.utc)
    b = dt.datetime.fromtimestamp(rows[-1]["ws"], dt.timezone.utc)
    return "%s .. %s" % (a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d"))
for nm, rows in (("TRAIN", TR), ("TEST", TE)):
    mat = sum((D(w.get("matched_pnl") or 0) for w in rows), D(0))
    res = sum((D(w.get("residual_pnl") or 0) for w in rows), D(0))
    mq = sum((D(w["matched"]) for w in rows), D(0))
    pc = sorted(float(D(w["pair_cost"])) for w in rows if w.get("pair_cost"))
    print("  %-6s n=%4d  %s" % (nm, len(rows), rng(rows)))
    print("         net %+10.2f  per window %+8.4f  net/matched_ctr %+9.5f"
          % (mat+res, (mat+res)/len(rows), (mat+res)/mq if mq else 0))
    print("         pair cost median %.4f   matched_q %.0f"
          % (pc[len(pc)//2] if pc else 0, mq))
json.dump([w["slug"] for w in TR], open("/tmp/claude-0/train.json", "w"))
json.dump([w["slug"] for w in TE], open("/tmp/claude-0/test.json", "w"))
print("\n  split saved: /tmp/claude-0/train.json, /tmp/claude-0/test.json")
print("\n  NOTE: the $480/day target is NOT used as evidence anywhere. It was")
print("  only ever a smell test on a 12.5x-off figure; it validates nothing.")
