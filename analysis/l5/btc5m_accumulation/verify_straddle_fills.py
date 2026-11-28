"""Are the straddle's double-fills REAL reversals, or data artifacts?

THE RISK THIS CHECKS. At X=0.08 the whole straddle result rests on 19
windows of 187 that filled BOTH legs (+15.96 against -8.96 from the other
168). A double fill requires the market to sit near-resolved one way and
then near-resolved the OTHER way inside five minutes. That is either a
genuine whipsaw or a data defect -- a crossed book, a stale quote, two
sides momentarily inconsistent -- and the two look identical in the P&L.

THE TEST. Record WHEN each leg filled. An artifact fills both legs within
a tick or two of each other, because the book was briefly inconsistent. A
real reversal takes time: the price has to travel from one extreme to the
other.

RESULT (2026-11-12, 189-window tape): median gap 100.0s, min 36.5s, max
198.5s. Zero pairs filled under 5s apart, zero under 30s. The second fill
lands at a median t=247s of 300. These are real reversals.

Usage: python3 -I analysis/l5/btc5m_accumulation/verify_straddle_fills.py
"""
import os,sys,statistics
sys.path.insert(0,os.getcwd()); os.environ['L5_BTC5M_CONTROLLER']='ON'
from decimal import Decimal as D
src=open('analysis/l5/btc5m_accumulation/straddle.py').read()
src=src.replace("if __name__ ==","if 0 and __name__ ==")
src=src.replace('HERE = os.path.dirname(os.path.abspath(__file__))',
                'HERE = os.path.join(os.getcwd(), 'analysis', 'l5', 'btc5m_accumulation')')
exec(compile(src,'straddle.py','exec'))
w=load(TAPE); X=D('0.08')
# re-walk recording WHEN each leg filled
def walk2(rows,x):
    won=outcome(rows)
    if won is None: return None
    filled={}; t=0.0
    while t < WINDOW_S-CANCEL_LEAD_S:
        got=quote_at(rows,t)
        if got is None: t+=0.5; continue
        q,_=got
        for s in (C.UP,C.DOWN):
            if s in filled: continue
            b=own_bid(q,s)
            if b is not None and b<=x: filled[s]=t
        t+=0.5
    return filled,won
gaps=[]
for s,rows in sorted(w.items()):
    r=walk2(rows,X)
    if not r: continue
    f,won=r
    if len(f)==2:
        gaps.append((s,abs(f[C.UP]-f[C.DOWN]),min(f.values()),max(f.values())))
print("DOUBLE-FILL WINDOWS at X=0.08: %d"%len(gaps))
print()
print("%-34s %9s %9s %9s"%("window","gap_s","first_t","second_t"))
print("-"*64)
for s,g,a,b in sorted(gaps,key=lambda r:r[1])[:12]:
    print("%-34s %9.1f %9.1f %9.1f"%(s[-18:],g,a,b))
print()
gs=[g for _,g,_,_ in gaps]
print("gap between the two fills: median %.1fs  min %.1fs  max %.1fs"%(statistics.median(gs),min(gs),max(gs)))
print("fills under 5s apart (suspicious/crossed): %d of %d"%(sum(1 for g in gs if g<5),len(gs)))
print("fills under 30s apart:                     %d of %d"%(sum(1 for g in gs if g<30),len(gs)))
seconds=[b for _,_,_,b in gaps]
print()
print("second (later) fill time in window: median %.0fs of 300"%statistics.median(seconds))
