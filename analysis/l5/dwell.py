import csv,collections,statistics as st
from decimal import Decimal as D
exec(open("/tmp/claude-0/-home-user-kalshi-arb-bot/e2b83131-1eb4-52c2-b749-df2a8ca69b61/scratchpad/sweep.py").read().split("def walk")[0])

def walk_dwell(rows,outc_w,terminal,dwell,min_exec=15.0):
    """Exit at the first tick that is >= `dwell` seconds after the residual
    first appeared AND passes every existing economic gate."""
    t_strand=None; last=None
    for row in rows:
        s=state(row)
        if s is None or s[0]>terminal: continue
        t,q,c,o,dom=s; last=s
        if t_strand is None: t_strand=t
        ex=max(D("0.01"),o-TICK); m=D(1)-ex; pair=c+m
        if c+(D(1)-o)<=MAKER_CEIL: continue
        if pair>PAIR_CEIL: continue
        if pair-D(1)+tfee(m)>=c: continue
        if t-t_strand<dwell: continue
        if terminal-t<=min_exec: continue
        return float(ex-c-tfee(m)),t,t-t_strand
    if last is None: return None
    t,q,c,o,dom=last
    return float(D(1 if dom==outc_w else 0)-c),None,None

def report(minutes,terminal,dwells):
    outc,seq=build(minutes)
    print(f"\n{'='*104}\n{minutes}-MIN WINDOWS   terminal T+{terminal}   "
          f"gate = dwell since stranding + the EXISTING economic gates\n{'='*104}")
    print(f"  {'dwell':<22} {'n':>4} {'mean':>9} {'p05':>9} {'p25':>9} {'worst':>9} "
          f"{'exit%':>6} {'<-0.30':>7} {'<-0.50':>7}")
    base=None
    for dw in dwells:
        vals=[];ex=[]
        for w,rows in seq.items():
            r=walk_dwell(rows,outc[w],terminal,dw)
            if r is None: continue
            vals.append(r[0]); ex.append(1 if r[1] is not None else 0)
        if len(vals)<30: continue
        v=sorted(vals); p=lambda f:v[max(0,round(f*(len(v)-1)))]
        t30=100*sum(1 for x in v if x<-0.30)/len(v)
        t50=100*sum(1 for x in v if x<-0.50)/len(v)
        print(f"  {str(dw)+'s':<22} {len(vals):>4} {st.mean(vals):>+9.4f} {p(0.05):>+9.4f} "
              f"{p(0.25):>+9.4f} {v[0]:>+9.4f} {100*st.mean(ex):>5.1f}% {t30:>6.1f}% {t50:>6.1f}%")
report("15",780,[0,15,30,60,120,240,480])
report("5",270,[0,15,30,60,120])
