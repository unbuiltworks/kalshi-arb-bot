import json, collections, math
F='analysis/l5/btc5m_accumulation/pmus_btc5m_quotes_20261112.jsonl'
w=collections.defaultdict(list)
for l in open(F):
    l=l.strip()
    if not l: continue
    try: r=json.loads(l)
    except ValueError: continue
    if r.get('bid') is None or r.get('ask') is None: continue
    w[r['slug']].append(r)
wins={}
for s,rs in w.items():
    rs.sort(key=lambda x:x.get('t') or 0)
    b,a=float(rs[-1]['bid']),float(rs[-1]['ask'])
    if b>=0.90: wins[s]='UP'
    elif a<=0.10: wins[s]='DOWN'
def px(r):
    b,a=float(r['bid']),float(r['ask']); return b,1.0-a
TARGET=0.96
print('OPEN -> EXTEND (average down) -> COMPLETE. TOTAL dollars, and shares deployed.')
print('one share per leg, so EXTEND deploys more shares when the position moves against us.')
print()
hdr=('open','maxext','n','shares','$ total','$/window','$/share','t(window)')
print('%-6s %-7s %-5s %-8s %-9s %-10s %-9s %s'%hdr)
for p in (0.60,0.70,0.80):
  for nx,step in ((0,0.0),(1,0.10),(2,0.10),(3,0.07)):
    per=[]; shares=0
    for s,rs in w.items():
        if s not in wins: continue
        fill=None
        for i,r in enumerate(rs):
            up,dn=px(r)
            fav,fp=('UP',up) if up>=dn else ('DOWN',dn)
            if 0.50<fp<=p: fill=(i,fav); break
        if fill is None: continue
        i,side=fill
        legs=[p]; nextpx=p-step
        for r in rs[i+1:]:
            if len(legs)>nx: break
            up,dn=px(r); own = up if side=='UP' else dn
            if step>0 and own<=nextpx and nextpx>0.02:
                legs.append(nextpx); nextpx-=step
        q=len(legs); cost=sum(legs); avg=cost/q
        shares+=q
        need=TARGET-avg
        done=False
        for r in rs[i+1:]:
            up,dn=px(r); opp = dn if side=='UP' else up
            if need>0.0 and opp<=need: done=True; break
        if done:
            per.append(q*(1.0-TARGET))          # q pairs locked
        else:
            per.append(q*((1.0-avg) if wins[s]==side else (-avg)))
    n=len(per); tot=sum(per); m=tot/n
    sd=(sum((x-m)**2 for x in per)/(n-1))**0.5
    t=m/(sd/math.sqrt(n)) if sd>0 else 0
    print('%-6.2f %-7d %-5d %-8d %-9.2f %-10.4f %-9.4f %+.2f'
          % (p,nx,n,shares,tot,m,tot/shares,t))
  print()
B=math.sqrt(2*math.log(12))
print('12 cells examined -> multiple-comparisons bar sqrt(2 ln 12) = %.2f' % B)
