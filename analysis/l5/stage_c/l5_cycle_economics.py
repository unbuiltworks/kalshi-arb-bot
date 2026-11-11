"""Where does L5's net value come from? Matched, residual, or neither."""
from __future__ import annotations
import json, statistics as st
from decimal import Decimal as D

W = [json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl")]
S = [w for w in W if w.get("has_redeem") and w.get("winner")]
print("windows: %d | with a venue redemption AND a known winner: %d\n"
      % (len(W), len(S)))

def dec(w, k):
    v = w.get(k)
    return None if v is None else D(v)

tot = sum((dec(w, "realized_net") for w in S), D(0))
mat = sum((dec(w, "matched_pnl") or D(0) for w in S), D(0))
res = sum((dec(w, "residual_pnl") or D(0) for w in S), D(0))
cost = sum((dec(w, "cost_total") for w in S), D(0))
red = sum((dec(w, "redeemed") for w in S), D(0))
mq = sum((dec(w, "matched") for w in S), D(0))
rq = sum((dec(w, "residual") for w in S), D(0))

print("=== AGGREGATE REALIZED, from L5's own redemptions ===")
print("  cash committed          %14.2f" % cost)
print("  redeemed                %14.2f" % red)
print("  REALIZED NET            %+14.2f" % tot)
print("  per window              %+14.4f" % (tot / len(S)))
print("\n=== DECOMPOSITION (matched + residual must equal realized) ===")
print("  matched inventory P&L   %+14.2f   (%6.1f%%)"
      % (mat, 100.0 * float(mat) / float(tot) if tot else 0))
print("  directional residual    %+14.2f   (%6.1f%%)"
      % (res, 100.0 * float(res) / float(tot) if tot else 0))
print("  sum                     %+14.2f   vs realized %+.2f  (resid %+.4f)"
      % (mat + res, tot, (mat + res) - tot))
print("\n  matched contracts       %14.0f" % mq)
print("  residual contracts      %14.0f  (%.1f%% of matched)"
      % (rq, 100.0 * float(rq) / float(mq) if mq else 0))
print("  matched P&L per contract %+13.6f" % (mat / mq if mq else 0))
print("  residual P&L per contract %+12.6f" % (res / rq if rq else 0))

pc = [float(dec(w, "pair_cost")) for w in S if w.get("pair_cost")]
pc.sort()
print("\n=== THE MATCHED PAIR COST, which is where matched P&L comes from ===")
print("  pair cost  p10=%.4f median=%.4f p90=%.4f mean=%.4f"
      % (pc[len(pc)//10], pc[len(pc)//2], pc[len(pc)*9//10],
         sum(pc)/len(pc)))
print("  pairs below $1.00: %d of %d (%.1f%%)"
      % (sum(1 for x in pc if x < 1.0), len(pc),
         100.0 * sum(1 for x in pc if x < 1.0) / len(pc)))
print("  NOTE our engine's opening cap is 0.96 and its completion line 1.00")

wins = [w for w in S if w.get("residual_won")]
loss = [w for w in S if w.get("residual_won") is False]
print("\n=== THE RESIDUAL IS A COIN FLIP, which is the point ===")
print("  residual won  %d  (%.1f%%)" % (len(wins), 100.0*len(wins)/len(S)))
print("  residual lost %d  (%.1f%%)" % (len(loss), 100.0*len(loss)/len(S)))
rw = sum((dec(w, "residual_pnl") for w in wins), D(0))
rl = sum((dec(w, "residual_pnl") for w in loss), D(0))
print("  residual P&L when it won  %+12.2f" % rw)
print("  residual P&L when it lost %+12.2f" % rl)
print("  net residual              %+12.2f" % (rw + rl))

prof = [w for w in S if dec(w, "realized_net") > 0]
print("\n=== PER-WINDOW OUTCOME ===")
nets = sorted(float(dec(w, "realized_net")) for w in S)
print("  profitable windows: %d of %d (%.1f%%)"
      % (len(prof), len(S), 100.0*len(prof)/len(S)))
print("  net  p10=%+.4f median=%+.4f p90=%+.4f"
      % (nets[len(nets)//10], nets[len(nets)//2], nets[len(nets)*9//10]))
print("  best %+.2f  worst %+.2f" % (nets[-1], nets[0]))
