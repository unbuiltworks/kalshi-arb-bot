"""Does the SETTLING reference rate lead the prediction-market book?

If the book is slow to absorb a reference move, the move is tradable before the
book catches up. The test is strictly causal: signal from [t-D, t], outcome from
(t, t+D]. Then the question that decides it -- is the predictable move larger
than the spread you must pay to act?
"""
import json, bisect, collections, statistics as st, math

W = json.load(open('analysis/profit_search/a_windows.json'))
print("SPREAD CENSUS (executable cost of acting), by family")
print("  %-8s %8s  %-9s %-9s %-9s %10s" % ("family","rows","med spread","p25","p75","med depth"))
fam=collections.defaultdict(list)
for w in W:
    f=w['market'].split('-')[1][:6]
    for r in w['rows']: fam[f].append(r)
for f in sorted(fam):
    g=fam[f]; sp=sorted(r['ask']-r['bid'] for r in g)
    dq=sorted(min(r['bidq'],r['askq']) for r in g)
    n=len(sp)
    print("  %-8s %8d  %-9.4f %-9.4f %-9.4f %10.1f"
          % (f,n,sp[n//2],sp[n//4],sp[3*n//4],dq[len(dq)//2]))

print("\nLEAD-LAG: reference move over [t-D,t]  ->  mid change over (t,t+D]")
print("  %-6s %8s %10s %10s %10s %12s" % ("D(s)","pairs","corr","beta(c/bp)","hit rate","med |dmid|"))
for D in (2.0, 5.0, 10.0, 30.0, 60.0):
    xs=[]; ys=[]
    for w in W:
        rows=w['rows']
        ts=[r['t'] for r in rows]
        for i,r in enumerate(rows):
            j=bisect.bisect_left(ts, r['t']-D)
            k=bisect.bisect_left(ts, r['t']+D)
            if j>=i or k>=len(rows): continue
            r0=rows[j]; r2=rows[k]
            if r0['rfr']<=0: continue
            sig=(r['rfr']-r0['rfr'])/r0['rfr']*1e4            # bp of reference move
            mid0=(r['bid']+r['ask'])/2; mid2=(r2['bid']+r2['ask'])/2
            if abs(sig)<1e-9: continue
            xs.append(sig); ys.append((mid2-mid0)*100.0)      # cents
    if len(xs)<200: print("  %-6.1f %8d  (too few)"%(D,len(xs))); continue
    mx,my=st.mean(xs),st.mean(ys)
    sx=st.pstdev(xs); sy=st.pstdev(ys)
    cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)
    corr=cov/(sx*sy) if sx>0 and sy>0 else float('nan')
    beta=cov/(sx*sx) if sx>0 else float('nan')
    hit=sum(1 for a,b in zip(xs,ys) if a*b>0)/len(xs)
    print("  %-6.1f %8d %10.4f %10.4f %10.3f %12.4f"
          % (D,len(xs),corr,beta,hit,st.median([abs(b) for b in ys])))
