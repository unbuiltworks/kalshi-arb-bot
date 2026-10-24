"""Held-out stranded-leg recovery, LOOKAHEAD-FREE.

The residual side, size and cost basis at decision time t are read from the
cumulative as-of-trade columns, so the policy sees only what existed at t.
Only the settlement outcome is taken from the end of the window, which is an
OUTCOME, not a decision input.

Path and outcome: L5's Polymarket window corpus. Fees: PMUS's own curve.
Polymarket fill economics are not transferred -- the cross is charged our
venue's taker fee plus a full 1-tick spread penalty.
"""
import csv, collections, statistics as st
from decimal import Decimal as D

BASE="/home/user/kalshi-arb-bot/analysis/l5"
TAKER_THETA=D("0.0695"); SPREAD=D("0.01")
def fee(p): return TAKER_THETA*p*(D(1)-p)

def build(minutes):
    outc={}
    for r in csv.DictReader(open(f"{BASE}/l5_windows_summary.csv")):
        if r["window_minutes"]==minutes and r["winning_outcome"] in ("Up","Down"):
            outc[r["window_id"]]=r["winning_outcome"]
    seq=collections.defaultdict(list)
    for r in csv.DictReader(open(f"{BASE}/l5_window_sequences.csv")):
        w=r["window_id"]
        if w not in outc: continue
        try:
            t=float(r["seconds_from_window_start"]); px=D(r["price"])
            eq=D(r["excess_qty"] or 0); ec=D(r["excess_cost"] or 0)
        except Exception: continue
        if not (D("0.005")<=px<=D("0.995")): continue
        up=px if r["outcome"]=="Up" else D(1)-px
        seq[w].append((t,up,eq,ec,r["dominant_side"]))
    for w in seq: seq[w].sort(key=lambda x:x[0])
    return outc,seq

def state_at(rows,t):
    v=None
    for x in rows:
        if x[0]<=t: v=x
        else: break
    return v

def run(minutes,ts):
    outc,seq=build(minutes)
    print(f"\n{'='*92}\n{minutes}-MINUTE WINDOWS, LOOKAHEAD-FREE   windows with prints: {len(seq)}\n{'='*92}")
    print(f"\nCALIBRATION of the AS-OF-t stranded side")
    print(f"  {'T+':>5} {'n':>5} {'mean px':>9} {'realized win':>13} {'gap':>8} {'side flips by end':>18}")
    for t in ts:
        xs=[];flip=0
        for w,rows in seq.items():
            s=state_at(rows,t)
            if s is None or s[4] not in ("Up","Down") or s[2]==0: continue
            ours=s[1] if s[4]=="Up" else D(1)-s[1]
            won=1 if s[4]==outc[w] else 0
            xs.append((float(ours),won))
            # how often the as-of-t side is NOT the end-of-window side
            if s[4]!=state_at(rows,10**9)[4]: flip+=1
        if len(xs)<30: continue
        mp=st.mean(x[0] for x in xs); wr=st.mean(x[1] for x in xs)
        print(f"  {t:>5} {len(xs):>5} {mp:>9.4f} {wr:>13.4f} {wr-mp:>+8.4f} {100*flip/len(xs):>17.1f}%")
    print(f"\nREALIZED RECOVERY per unit of then-stranded inventory")
    print(f"  {'T+':>5} {'n':>5} {'HOLD':>9} {'CROSS':>9} {'CROSS-HOLD':>11} {'t-stat':>7} "
          f"{'HOLD p05':>9} {'CROSS p05':>9} {'HOLDworst':>10} {'CROSSworst':>11}")
    out=[]
    for t in ts:
        h,c=[],[]
        for w,rows in seq.items():
            s=state_at(rows,t)
            if s is None or s[4] not in ("Up","Down") or s[2]==0 or s[3]<=0: continue
            q=abs(s[2]); cb=s[3]/q
            if not (D("0.01")<=cb<=D("0.99")): continue
            ours=s[1] if s[4]=="Up" else D(1)-s[1]
            ex=max(D("0.01"),ours-SPREAD)
            won=1 if s[4]==outc[w] else 0
            h.append(float(D(won)-cb)); c.append(float(ex-cb-fee(D(1)-ex)))
        if len(h)<30: continue
        d=[c[i]-h[i] for i in range(len(h))]
        md=st.mean(d); sd=st.pstdev(d) or 1e-9
        tstat=md/(sd/len(d)**0.5)
        p5=lambda a:sorted(a)[max(0,round(0.05*(len(a)-1)))]
        print(f"  {t:>5} {len(h):>5} {st.mean(h):>+9.4f} {st.mean(c):>+9.4f} {md:>+11.4f} "
              f"{tstat:>7.2f} {p5(h):>+9.4f} {p5(c):>+9.4f} {min(h):>+10.4f} {min(c):>+11.4f}")
        out.append((t,md,tstat,len(h),st.mean(h),st.mean(c)))
    return out

a=run("15",[120,180,240,300,360,420,480,540,600,660,720,780])
b=run("5",[30,60,90,120,150,180,210,240,270])
