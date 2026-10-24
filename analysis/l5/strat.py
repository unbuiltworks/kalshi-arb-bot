"""Does the cross/hold choice depend on how far underwater the leg is?

Stratify the lookahead-free held-out cases by moneyness o - c (current own
price minus cost basis) and compare A3/A4 MARKET SELL against A5 HOLD.
"""
import csv, collections, statistics as st
from decimal import Decimal as D
BASE="/home/user/kalshi-arb-bot/analysis/l5"
TK=D("0.0695"); TICK=D("0.01")
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

def cases_at(outc,seq,t_dec):
    out=[]
    for w,rows in seq.items():
        s=None
        for x in rows:
            if x[0]<=t_dec: s=x
            else: break
        if s is None or s[4] not in ("Up","Down") or s[2]==0 or s[3]<=0: continue
        q=abs(s[2]); c=s[3]/q
        if not (D("0.01")<=c<=D("0.99")): continue
        o=s[1] if s[4]=="Up" else D(1)-s[1]
        out.append((c,o,1 if s[4]==outc[w] else 0))
    return out

def report(minutes,decs):
    outc,seq=build(minutes)
    allc=[]
    for t in decs: allc+= [(t,)+c for c in cases_at(outc,seq,t)]
    print(f"\n{'='*96}\n{minutes}-MIN WINDOWS, pooled over decision times {decs}   n={len(allc)}\n{'='*96}")
    bands=[(D("-1.00"),D("-0.20"),"deeply underwater  o-c < -0.20"),
           (D("-0.20"),D("-0.10"),"underwater        -0.20..-0.10"),
           (D("-0.10"),D("-0.03"),"mildly under      -0.10..-0.03"),
           (D("-0.03"),D("0.03"), "at the money      -0.03..+0.03"),
           (D("0.03"),D("0.10"),  "mildly up         +0.03..+0.10"),
           (D("0.10"),D("1.00"),  "well up           o-c > +0.10")]
    print(f"  {'band':<34} {'n':>5} {'HOLD':>9} {'CROSS':>9} {'CROSS-HOLD':>11} {'t':>6} "
          f"{'HOLDp05':>9} {'CROSSp05':>9}")
    for lo,hi,lbl in bands:
        h,x=[],[]
        for t,c,o,wn in allc:
            if not (lo<=o-c<hi): continue
            ex=max(D("0.01"),o-TICK)
            h.append(float(D(wn)-c)); x.append(float(ex-c-tfee(D(1)-ex)))
        if len(h)<40: continue
        d=[x[i]-h[i] for i in range(len(h))]
        sd=st.pstdev(d) or 1e-9
        p5=lambda a:sorted(a)[max(0,round(0.05*(len(a)-1)))]
        print(f"  {lbl:<34} {len(h):>5} {st.mean(h):>+9.4f} {st.mean(x):>+9.4f} "
              f"{st.mean(d):>+11.4f} {st.mean(d)/(sd/len(d)**0.5):>6.2f} "
              f"{p5(h):>+9.4f} {p5(x):>+9.4f}")

report("15",[300,420,480,600])
report("5",[90,150,210])
