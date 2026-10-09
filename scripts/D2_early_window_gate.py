"""Item 5 precondition: does restricting Kalshi entry to the EARLY window
turn the negative expectancy positive? This is the configuration I proposed
for a live probe but never tested historically. It is free to test, and if
it is negative there is nothing to probe.
"""
import json, glob, collections, math, random, statistics, sys

def f(x,d=None):
    try: return float(x)
    except: return d
def fee(c,p,maker):
    r=0.0175 if maker else 0.07
    return math.ceil(r*c*p*(1-p)*100)/100.0
def ci(v,rng,reps=1500,cap=20000):
    v=[x for x in v if x is not None]
    if len(v)<2: return None,None
    k,m=len(v),min(len(v),cap)
    o=sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)]) for _ in range(reps))
    return o[int(reps*.025)],o[int(reps*.975)]

rng=random.Random(20261009)
paths=[]
for g in sys.argv[1:-1]: paths.extend(sorted(glob.glob(g)))
mk=collections.defaultdict(lambda:{'lad':[],'pr':[],'close':None})
for p in paths:
    for l in open(p,encoding='utf-8',errors='ignore'):
        try: r=json.loads(l)
        except: continue
        if r.get('venue')!='kalshi': continue
        m,k,t=r.get('market'),r.get('kind'),r.get('t')
        if not m or t is None: continue
        if k=='window':
            import datetime as dt
            ct=r.get('close_time')
            if ct:
                mk[m]['close']=dt.datetime.fromisoformat(ct.replace('Z','+00:00')).timestamp()
        elif k=='bbo':
            yb=r.get('yes_bids') or []; nb=r.get('no_bids') or []
            byb=max((f(x) for x,_ in yb),default=None); bnb=max((f(x) for x,_ in nb),default=None)
            if byb is not None and bnb is not None:
                mk[m]['lad'].append((t,1-bnb,1-byb,yb,nb))
        elif k=='print':
            yp=f(r.get('yes_price')); cnt=f(r.get('count'),0.0); ts=r.get('taker_side')
            if yp is not None and ts in ('yes','no'): mk[m]['pr'].append((t,yp,cnt,ts))
for m in mk:
    mk[m]['lad'].sort(); mk[m]['pr'].sort()

out={'_what':'Kalshi AGG and PAS restricted to entry while seconds_to_close > MIN_TTC. '
             'Settlement = last-print proxy. Every market settled including one-sided.'}
for MIN_TTC in (0,120,300,420,600):
    for TH in (0.35,0.40,0.45,0.50):
        for mode in ('AGG','PAS'):
            pnls=[]; nboth=nany=0
            for m,d in mk.items():
                if not d['pr'] or d['close'] is None: continue
                winner='YES' if d['pr'][-1][1]>0.5 else 'NO'
                fills={}
                if mode=='AGG':
                    for t,ya,na,_,_ in d['lad']:
                        if d['close']-t <= MIN_TTC: continue
                        if 'YES' not in fills and ya<=TH: fills['YES']=ya
                        if 'NO' not in fills and na<=TH: fills['NO']=na
                else:
                    ahead={}; 
                    for t,ya,na,yb,nb in d['lad']:
                        if d['close']-t <= MIN_TTC: continue
                        if 'YES' not in ahead and 'YES' not in fills:
                            ahead['YES']=sum(f(s,0.0) for p,s in yb if f(p,0)>=TH)
                        if 'NO' not in ahead and 'NO' not in fills:
                            ahead['NO']=sum(f(s,0.0) for p,s in nb if f(p,0)>=TH)
                    for t,yp,cnt,ts in d['pr']:
                        if d['close']-t <= MIN_TTC: continue
                        if ts=='no' and yp<=TH and 'YES' in ahead:
                            ahead['YES']-=cnt
                            if ahead['YES']<0: fills['YES']=TH; del ahead['YES']
                        if ts=='yes' and (1-yp)<=TH and 'NO' in ahead:
                            ahead['NO']-=cnt
                            if ahead['NO']<0: fills['NO']=TH; del ahead['NO']
                if len(fills)==2: nboth+=1
                if fills: nany+=1
                if not fills: pnls.append(0.0); continue
                cost=sum(px+fee(1,px,mode=='PAS') for px in fills.values())
                proc=1.0 if len(fills)==2 else (1.0 if next(iter(fills))==winner else 0.0)
                pnls.append(proc-cost)
            if not pnls: continue
            lo,hi=ci(pnls,rng)
            out[f'ttc>{MIN_TTC}s_theta{TH:.2f}_{mode}']={
              'markets':len(pnls),'both_legs':round(nboth/len(pnls),4),
              'engaged':round(nany/len(pnls),4),
              'mean_pnl':round(statistics.fmean(pnls),5),
              'ci95':[None if lo is None else round(lo,5),None if hi is None else round(hi,5)],
              'verdict':'POSITIVE' if lo and lo>0 else ('NEGATIVE' if hi and hi<0 else 'INDETERMINATE')}
json.dump(out,open(sys.argv[-1],'w'),indent=1)
rows=[(k,v) for k,v in out.items() if not k.startswith('_')]
rows.sort(key=lambda kv:-kv[1]['mean_pnl'])
print('TOP 12 configurations by mean P&L:')
for k,v in rows[:12]:
    print('  %-30s both %.3f eng %.3f  mean %8.5f  CI [%8.5f,%8.5f]  %s'%(k,v['both_legs'],v['engaged'],v['mean_pnl'],v['ci95'][0],v['ci95'][1],v['verdict']))
print()
print('any POSITIVE? ', [k for k,v in rows if v['verdict']=='POSITIVE'] or 'NONE')
