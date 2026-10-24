"""Head-to-head: existing defensive-completion policy vs the candidate.

Both policies are walked tick by tick over the lookahead-free held-out paths,
from the first moment a residual exists until the terminal. Each may submit
ONE crossing order. Economics use PMUS fees and charge the cross a full
1-tick spread penalty.

  EXISTING  arm only inside [terminal-60, terminal-15); refuse while a
            profitable maker completion (pair <= 0.96) is reachable; pair
            ceiling 1.20; exit must reduce the bounded downside.
  CANDIDATE evaluate at EVERY tick once the profitable completion is not
            reachable; cross when the exit caps the position's loss at or
            below LOSS_CAP per unit; same 1.20 pair ceiling; one order.
"""
import csv, collections, statistics as st
from decimal import Decimal as D
BASE="/home/user/kalshi-arb-bot/analysis/l5"
TK=D("0.0695"); TICK=D("0.01")
MAKER_CEIL=D("0.96"); PAIR_CEIL=D("1.20")
ARM_S=60.0; MIN_EXEC_S=15.0
def tfee(p): return TK*p*(D(1)-p)

def build(minutes):
    outc={r["window_id"]:r["winning_outcome"]
          for r in csv.DictReader(open(f"{BASE}/l5_windows_summary.csv"))
          if r["window_minutes"]==minutes and r["winning_outcome"] in ("Up","Down")}
    seq=collections.defaultdict(list)
    for r in csv.DictReader(open(f"{BASE}/l5_window_sequences.csv")):
        w=r["window_id"]
        if w not in outc: continue
        try:
            t=float(r["seconds_from_window_start"]); px=D(r["price"])
            eq=D(r["excess_qty"] or 0); ec=D(r["excess_cost"] or 0)
        except Exception: continue
        if not (D("0.005")<=px<=D("0.995")): continue
        seq[w].append((t,px if r["outcome"]=="Up" else D(1)-px,eq,ec,r["dominant_side"]))
    for w in seq: seq[w].sort(key=lambda x:x[0])
    return outc,seq

def walk(rows,outc_w,terminal,policy,loss_cap=None):
    """Return realized P&L per unit, and when/at what price we exited."""
    for t,up,eq,ec,dom in rows:
        if t>terminal: break
        if dom not in ("Up","Down") or eq==0 or ec<=0: continue
        q=abs(eq); c=ec/q
        if not (D("0.01")<=c<=D("0.99")): continue
        o=up if dom=="Up" else D(1)-up                 # our side's price
        ex=max(D("0.01"),o-TICK)                       # executable exit
        m=D(1)-ex                                      # complement own price
        pair=c+m
        maker_pair=c+(D(1)-o)                          # complement AT the touch
        if maker_pair<=MAKER_CEIL:                     # priority one, both policies
            continue
        if pair>PAIR_CEIL: continue
        hold_down=c                                    # worst case holding, per unit
        exit_down=pair-D(1)+tfee(m)                    # worst case exiting, per unit
        if exit_down>=hold_down: continue
        if policy=="existing":
            rem=terminal-t
            if not (ARM_S>=rem>MIN_EXEC_S): continue
        else:
            if (ex-c-tfee(m))< -loss_cap: continue     # only while the cap still binds
        return float(ex-c-tfee(m)),t,float(o),float(c)
    # never exited
    last=None
    for t,up,eq,ec,dom in rows:
        if t<=terminal and dom in ("Up","Down") and eq!=0 and ec>0: last=(t,up,eq,ec,dom)
    if last is None: return None
    t,up,eq,ec,dom=last; q=abs(eq); c=ec/q
    if not (D("0.01")<=c<=D("0.99")): return None
    won=1 if dom==outc_w else 0
    return float(D(won)-c),None,None,float(c)

def report(minutes,terminal):
    outc,seq=build(minutes)
    arms={"EXISTING (late arm window)":("existing",None)}
    for cap in ("0.02","0.05","0.10","0.15","0.20","0.30"):
        arms[f"CANDIDATE loss cap {cap}/unit"]=("cand",D(cap))
    arms["A5 HOLD always (no recovery)"]=("hold",None)
    print(f"\n{'='*104}\n{minutes}-MIN WINDOWS   terminal T+{terminal}   "
          f"head-to-head, one crossing order max\n{'='*104}")
    print(f"  {'policy':<32} {'n':>5} {'mean':>9} {'p05':>9} {'p25':>9} {'worst':>9} "
          f"{'exits':>7} {'mean exit T+':>12}")
    for name,(pol,cap) in arms.items():
        vals=[];exits=[];ts=[]
        for w,rows in seq.items():
            if pol=="hold":
                r=walk(rows,outc[w],terminal,"existing_never",None)
            else:
                r=walk(rows,outc[w],terminal,"existing" if pol=="existing" else "cand",cap)
            if r is None: continue
            vals.append(r[0])
            if r[1] is not None: exits.append(1); ts.append(r[1])
            else: exits.append(0)
        if len(vals)<30: continue
        v=sorted(vals)
        p=lambda f:v[max(0,round(f*(len(v)-1)))]
        mt=f"{st.mean(ts):.0f}" if ts else "-"
        print(f"  {name:<32} {len(vals):>5} {st.mean(vals):>+9.4f} {p(0.05):>+9.4f} "
              f"{p(0.25):>+9.4f} {v[0]:>+9.4f} {100*st.mean(exits):>6.1f}% {mt:>12}")

report("15",780)
report("5",270)
