"""Native-venue short-horizon predictability on Kalshi BTC15m.

No external feed, so no resolution gap: the signal is the venue's own
aggressor-tagged print flow (~10/sec) and its top-of-book. Strictly causal --
signal from [t-D, t], target from (t, t+D]. Chronological split: the estimator
only ever sees the earlier half.
"""
import json, glob, bisect, collections, statistics as st

def load(series="KXBTC15M"):
    bbo=collections.defaultdict(list); pr=collections.defaultdict(list); meta={}
    for fn in sorted(glob.glob('data/policy_d_port/tape_*.jsonl')
                     +glob.glob('data/policy_d_port_ext/tape_*.jsonl')):
        for l in open(fn,errors='replace'):
            try: d=json.loads(l)
            except: continue
            if d.get('venue')!='kalshi': continue
            m=d.get('market') or ''
            if not m.startswith(series): continue
            if d['kind']=='bbo':
                yb=d.get('yes_bids') or []; nb=d.get('no_bids') or []
                if not yb or not nb: continue
                bbo[m].append((d['t'],float(yb[0][0]),float(yb[0][1]),
                               float(nb[0][0]),float(nb[0][1])))
            elif d['kind']=='print':
                pr[m].append((d['t'],float(d['yes_price']),
                              float(d.get('count') or 0),d.get('taker_side')))
            elif d['kind']=='window':
                meta[m]=d.get('close_time')
    for v in bbo.values(): v.sort()
    for v in pr.values(): v.sort()
    return bbo,pr,meta

def samples(bbo,pr,D):
    """(signed flow imbalance, book imbalance, spread, future mid change in cents)"""
    out=[]
    for m in bbo:
        B=bbo[m]; P=pr.get(m) or []
        if len(B)<6 or len(P)<20: continue
        bt=[x[0] for x in B]; pt=[x[0] for x in P]
        for i,(t,yb,ybq,nb,nbq) in enumerate(B):
            j=bisect.bisect_left(bt,t+D)
            if j>=len(B): continue
            lo=bisect.bisect_left(pt,t-D); hi=bisect.bisect_left(pt,t)
            if hi-lo < 3: continue
            buy=sum(q for _,_,q,s in P[lo:hi] if s=='yes')
            sell=sum(q for _,_,q,s in P[lo:hi] if s=='no')
            if buy+sell<=0: continue
            flow=(buy-sell)/(buy+sell)                      # [-1,1]
            bimb=(ybq-nbq)/(ybq+nbq) if ybq+nbq>0 else 0.0
            mid0=(yb+(1.0-nb))/2.0
            _,yb2,_,nb2,_=B[j]; mid2=(yb2+(1.0-nb2))/2.0
            out.append((t,m,flow,bimb,(1.0-yb-nb),(mid2-mid0)*100.0))
    out.sort()
    return out

def corr(xs,ys):
    if len(xs)<30: return float('nan'),float('nan')
    mx,my=st.mean(xs),st.mean(ys); sx,sy=st.pstdev(xs),st.pstdev(ys)
    if sx<=0 or sy<=0: return float('nan'),float('nan')
    cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)
    return cov/(sx*sy), cov/(sx*sx)

if __name__=="__main__":
    bbo,pr,meta=load()
    print("KXBTC15M markets: %d   bbo rows %d   prints %d"
          % (len(bbo),sum(len(v) for v in bbo.values()),sum(len(v) for v in pr.values())))
    for D in (5.0,15.0,30.0,60.0):
        S=samples(bbo,pr,D)
        if len(S)<200: print("  D=%4.0fs  n=%d too few"%(D,len(S))); continue
        half=len(S)//2
        TR,TE=S[:half],S[half:]
        print("\n  D=%.0fs   n=%d  (train %d / test %d, chronological)" % (D,len(S),len(TR),len(TE)))
        for nm,sig in (("taker flow imbalance",2),("book imbalance",3)):
            ctr,btr=corr([s[sig] for s in TR],[s[5] for s in TR])
            cte,bte=corr([s[sig] for s in TE],[s[5] for s in TE])
            print("    %-24s TRAIN corr %+0.4f beta %+0.3f c | TEST corr %+0.4f beta %+0.3f c"
                  % (nm,ctr,btr,cte,bte))
        print("    median |future mid move| %.2f c    median spread %.2f c"
              % (st.median([abs(s[5]) for s in TE]), st.median([s[4]*100 for s in TE])))
