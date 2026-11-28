"""L5's realized economics, on ONLY the windows whose history reconciles.

WHY THE FILTER IS THE WHOLE METHODOLOGY. `realized = redeemed - cost` is only
meaningful if we can see every trade that built the position. We cannot: the
public activity feed is paginated and lossy, and for 59.8% of windows L5
redeemed MORE than the trades we can see would have bought -- in the worst
cases about twice as much. Taking those windows at face value produces
+$62.71 per window, which is $6,020/day against a $480/day target: a 12.5x
overstatement, and a loud signal that the input is wrong rather than that L5
is twelve times better than believed.

A window reconciles when the dollars L5 redeemed equal the winning-side
holding the trades we can see would have produced, within 1%. On those
windows, and only those, `redeemed - cost` is a real realized figure and the
matched/residual split must add up to it.
"""
from __future__ import annotations
import json
from decimal import Decimal as D

W = [json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl")]
S = [w for w in W if w.get("has_redeem") and w.get("winner")]

def ok(w, tol=D("0.01")):
    hold = D(w["up"]) if w["winner"] == "Up" else D(w["dn"])
    if hold <= 0:
        return False
    r = D(w["redeemed"]) / hold
    return abs(r - D(1)) <= tol

R = [w for w in S if ok(w)]
print("windows with redemption+winner: %d" % len(S))
print("of which the trade history RECONCILES within 1%%: %d (%.1f%%)\n"
      % (len(R), 100.0*len(R)/len(S)))

def agg(rows, name):
    n = len(rows)
    if not n:
        return
    tot = sum((D(w["realized_net"]) for w in rows), D(0))
    mat = sum((D(w.get("matched_pnl") or 0) for w in rows), D(0))
    res = sum((D(w.get("residual_pnl") or 0) for w in rows), D(0))
    cost = sum((D(w["cost_total"]) for w in rows), D(0))
    mq = sum((D(w["matched"]) for w in rows), D(0))
    rq = sum((D(w["residual"]) for w in rows), D(0))
    print("=== %s (n=%d) ===" % (name, n))
    print("  cash committed        %13.2f" % cost)
    print("  REALIZED NET          %+13.2f    per window %+9.4f"
          % (tot, tot / n))
    print("  matched inventory P&L %+13.2f    (%6.1f%% of net)"
          % (mat, 100.0*float(mat)/float(tot) if tot else 0))
    print("  directional residual  %+13.2f    (%6.1f%% of net)"
          % (res, 100.0*float(res)/float(tot) if tot else 0))
    print("  matched + residual    %+13.2f    unexplained %+.4f"
          % (mat+res, (mat+res)-tot))
    print("  matched contracts %12.0f   P&L/contract %+9.6f"
          % (mq, mat/mq if mq else 0))
    print("  residual contracts%12.0f   P&L/contract %+9.6f"
          % (rq, res/rq if rq else 0))
    print("  residual as %% of matched            %8.1f%%"
          % (100.0*float(rq)/float(mq) if mq else 0))
    nets = sorted(float(D(w["realized_net"])) for w in rows)
    prof = sum(1 for x in nets if x > 0)
    print("  profitable windows    %d of %d (%.1f%%)"
          % (prof, n, 100.0*prof/n))
    print("  net p10=%+.4f median=%+.4f p90=%+.4f"
          % (nets[len(nets)//10], nets[len(nets)//2], nets[len(nets)*9//10]))
    pc = sorted(float(D(w["pair_cost"])) for w in rows if w.get("pair_cost"))
    if pc:
        print("  pair cost p10=%.4f median=%.4f p90=%.4f  below $1: %.1f%%"
              % (pc[len(pc)//10], pc[len(pc)//2], pc[len(pc)*9//10],
                 100.0*sum(1 for x in pc if x < 1.0)/len(pc)))
    rw = [w for w in rows if w.get("residual_won")]
    print("  residual won %d of %d (%.1f%%)"
          % (len(rw), n, 100.0*len(rw)/n))
    print("  implied per day at 96 windows        %+9.2f" % (tot / n * 96))
    print()

agg(R, "RECONCILED windows -- the trustworthy set")
agg(S, "ALL windows -- NOT trustworthy, shown for contrast")
