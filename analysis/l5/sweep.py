"""The one knob that matters: how long the complement is given before the
bounded exit may act.

Every arm keeps the EXISTING economic gates unchanged -- priority one is the
profitable maker completion (pair <= 0.96), the 1.20 pair ceiling, and the
requirement that exiting reduce the bounded downside. The ONLY difference is
the interval in which the exit may be evaluated:

   EXISTING : [terminal-60, terminal-15)        a 45s window, sampled
   ARM(G)   : [G, terminal)                     continuous from G onward

Lookahead-free: side, size and cost basis are the as-of-tick cumulative state.
PMUS fees; the cross is charged a full 1-tick spread penalty.
"""
import csv, collections, statistics as st
from decimal import Decimal as D
BASE="/home/user/kalshi-arb-bot/analysis/l5"
TK=D("0.0695"); TICK=D("0.01"); MAKER_CEIL=D("0.96"); PAIR_CEIL=D("1.20")
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

def state(row):
    t,up,eq,ec,dom=row
    if dom not in ("Up","Down") or eq==0 or ec<=0: return None
    q=abs(eq); c=ec/q
    if not (D("0.01")<=c<=D("0.99")): return None
    o=up if dom=="Up" else D(1)-up
    return t,q,c,o,dom

def walk(rows,outc_w,terminal,lo,hi):
    """Cross at the first tick in [lo,hi) where every existing gate allows it."""
    last=None
    for row in rows:
        s=state(row)
        if s is None or s[0]>terminal: continue
        t,q,c,o,dom=s; last=s
        ex=max(D("0.01"),o-TICK); m=D(1)-ex; pair=c+m
        if c+(D(1)-o)<=MAKER_CEIL: continue          # priority one still reachable
        if pair>PAIR_CEIL: continue
        if pair-D(1)+tfee(m)>=c: continue            # must reduce bounded downside
        if not (lo<=t<hi): continue
        return float(ex-c-tfee(m)),t
    if last is None: return None
    t,q,c,o,dom=last
    return float(D(1 if dom==outc_w else 0)-c),None

def report(minutes,terminal,Gs):
    outc,seq=build(minutes)
    print(f"\n{'='*106}\n{minutes}-MIN WINDOWS   terminal T+{terminal}\n{'='*106}")
    print(f"  {'policy':<36} {'n':>4} {'mean':>9} {'p05':>9} {'p25':>9} {'worst':>9} "
          f"{'exit%':>6} {'meanT+':>7} {'<-0.30':>7}")
    arms=[("A5 HOLD always (no recovery)",1e9,1e9),
          ("EXISTING late arm [T-60,T-15)",terminal-60,terminal-15)]
    arms+= [(f"ARM(G={g}) continuous [G,terminal)",g,terminal) for g in Gs]
    for name,lo,hi in arms:
        vals=[];ex=[];ts=[]
        for w,rows in seq.items():
            r=walk(rows,outc[w],terminal,lo,hi)
            if r is None: continue
            vals.append(r[0]); ex.append(1 if r[1] is not None else 0)
            if r[1] is not None: ts.append(r[1])
        if len(vals)<30: continue
        v=sorted(vals); p=lambda f:v[max(0,round(f*(len(v)-1)))]
        tail=100*sum(1 for x in v if x< -0.30)/len(v)
        print(f"  {name:<36} {len(vals):>4} {st.mean(vals):>+9.4f} {p(0.05):>+9.4f} "
              f"{p(0.25):>+9.4f} {v[0]:>+9.4f} {100*st.mean(ex):>5.1f}% "
              f"{(f'{st.mean(ts):.0f}' if ts else '-'):>7} {tail:>6.1f}%")

report("15",780,[180,300,420,480,540,600,660,720])
report("5",270,[60,90,120,150,180,210,240])
