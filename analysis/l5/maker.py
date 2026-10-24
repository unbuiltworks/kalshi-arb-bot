"""Held-out test of the five recovery actions, lookahead-free.

On a single-instrument venue "buy the complement" and "sell the held leg" are
ONE wire order (l5_defensive_completion verification 2), so the five actions
reduce to three distinct orders plus the do-nothing:

  A1 keep resting / A2 reprice -> LIMIT SELL of the held leg at o + j ticks
  A3 acquire complement / A4 direct exit -> MARKET SELL at o - 1 tick
  A5 hold                                -> no order

Fills are charged PMUS fees. A maker fill requires the path to REACH the
level, and a queue-aware variant requires it to pass one tick BEYOND.
"""
import csv, collections, statistics as st
from decimal import Decimal as D

BASE="/home/user/kalshi-arb-bot/analysis/l5"
TK=D("0.0695"); MK=D("0.0125"); TICK=D("0.01")
def tfee(p): return TK*p*(D(1)-p)
def mcred(p): return MK*p*(D(1)-p)

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

def run(minutes,t_dec,terminal):
    outc,seq=build(minutes)
    print(f"\n{'='*94}\n{minutes}-MIN WINDOWS   decision at T+{t_dec}, terminal T+{terminal}"
          f"   (lookahead-free)\n{'='*94}")
    cases=[]
    for w,rows in seq.items():
        s=None
        for x in rows:
            if x[0]<=t_dec: s=x
            else: break
        if s is None or s[4] not in ("Up","Down") or s[2]==0 or s[3]<=0: continue
        q=abs(s[2]); c=s[3]/q
        if not (D("0.01")<=c<=D("0.99")): continue
        o=s[1] if s[4]=="Up" else D(1)-s[1]              # our side's price now
        fut=[(x[0], x[1] if s[4]=="Up" else D(1)-x[1])
             for x in rows if t_dec<x[0]<=terminal]       # our side's future path
        won=1 if s[4]==outc[w] else 0
        cases.append((q,c,o,fut,won))
    if len(cases)<30: print("  too few cases"); return
    print(f"  cases n={len(cases)}   mean cost basis {st.mean(float(x[1]) for x in cases):.4f}"
          f"   mean own price {st.mean(float(x[2]) for x in cases):.4f}")
    def summarize(name,vals,fills=None):
        v=sorted(vals); p5=v[max(0,round(0.05*(len(v)-1)))]
        extra=f"  fill {100*fills/len(vals):>5.1f}%" if fills is not None else " "*13
        print(f"  {name:<34} mean {st.mean(vals):>+8.4f}  p05 {p5:>+8.4f}  "
              f"worst {v[0]:>+8.4f}{extra}")
        return st.mean(vals)
    res={}
    res["A5 HOLD to settlement"]=summarize("A5 HOLD to settlement",
        [float(D(wn)-c) for q,c,o,f,wn in cases])
    res["A3/A4 MARKET SELL now"]=summarize("A3/A4 MARKET SELL now",
        [float(max(D("0.01"),o-TICK)-c-tfee(D(1)-max(D("0.01"),o-TICK))) for q,c,o,f,wn in cases])
    for j in (0,1,2,3):
        for strict,lbl in ((False,""),(True," queue-aware")):
            vals=[];nf=0
            for q,c,o,f,wn in cases:
                lvl=o+j*TICK
                need=lvl+TICK if strict else lvl
                hit=any(p>=need for _,p in f)
                if hit:
                    nf+=1; vals.append(float(lvl-c+mcred(lvl)))
                else:
                    vals.append(float(D(wn)-c))
            res[f"A1/A2 LIMIT SELL +{j}t{lbl}"]=summarize(f"A1/A2 LIMIT SELL +{j} tick{lbl}",vals,nf)
    # the candidate: rest the limit, and if it has not filled by the terminal, cross
    for j in (0,1,2):
        vals=[];nf=0
        for q,c,o,f,wn in cases:
            lvl=o+j*TICK
            hit=any(p>=lvl+TICK for _,p in f)
            if hit: nf+=1; vals.append(float(lvl-c+mcred(lvl)))
            else:
                last=f[-1][1] if f else o
                ex=max(D("0.01"),last-TICK)
                vals.append(float(ex-c-tfee(D(1)-ex)))
        res[f"CANDIDATE limit +{j}t then cross"]=summarize(
            f"CANDIDATE limit +{j}t, cross at terminal",vals,nf)
    best=max(res.items(),key=lambda kv:kv[1])
    print(f"\n  best by mean: {best[0]}  {best[1]:+.4f}/unit")

for t in (300,480,600):
    run("15",t,780)
run("5",150,270)
