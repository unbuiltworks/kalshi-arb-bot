import json,gzip,collections
R='/home/user/kalshi-arb-bot/analysis/l5/rewards/'
d=json.load(gzip.open(R+'sports_orders_fills_20261114_20261118.json.gz'))
p=json.load(open(R+'sports_market_pnl_20261114_20261118.json'))
f=sorted([x for x in d if x['kind']=='FILL'],key=lambda x:x['t'])
m=collections.defaultdict(lambda:{'YES':[0.0,0.0],'NO':[0.0,0.0],'fills':[]})
for x in f:
    q=float(x['qty']);px=float(x['px']); a=m[x['slug']][x['side']]; a[0]+=q;a[1]+=q*px; m[x['slug']]['fills'].append((x['t'],x['side'],px,q))
out={};tq=tc=tp=tpp=0
for s,v in m.items():
    y,yc=v['YES'];n,nc=v['NO'];pairs=min(y,n)
    tq+=y+n;tc+=yc+nc;tp+=pairs
    side=None;rq=abs(y-n);rc=0
    if rq>1e-9:
        side='YES' if y>n else 'NO'; rc=rq*(yc/y if y>n else nc/n)
    pp=pairs-(yc+nc-rc); tpp+=pp
    out[s]=dict(y=y,yc=yc,n=n,nc=nc,pairs=pairs,pair_pnl=pp,res_side=side,res_qty=rq,res_cost=rc,first=v['fills'][0][0],last=v['fills'][-1][0],fills=v['fills'])
print(len(out),tq,tc,tp,tpp)
mine={s for s,v in out.items() if v['res_side']}; theirs={s for s,v in p.items() if v['res_qty']}
print(len(mine),len(theirs),mine-theirs,theirs-mine, sum(out[s]['res_cost'] for s in mine))
for s in mine&theirs:
    a,b=out[s],p[s]
    if a['res_side']!=b['res_side'] or abs(a['res_qty']-b['res_qty'])>1e-6 or abs(a['res_cost']-b['res_cost'])>1e-6: print('DIFF',s,a['res_side'],a['res_qty'],a['res_cost'],b['res_side'],b['res_qty'],b['res_cost'])
print([s for s in out if not s.startswith('aec-')], [s for s in mine if not s.startswith('aec-')])
print(sum(1 for s in p if s not in out and p[s]['fills']), len(p))
json.dump(out,open('rebuild.json','w'))
for s in sorted(mine): 
    a=out[s]; print(s,a['res_side'],round(a['res_qty'],3),round(a['res_cost'],3),p[s]['settle_px'],a['last'][:16])
print({k:p['aec-utr-gaskan-hanbra-2026-11-17'][k] for k in list(p['aec-utr-gaskan-hanbra-2026-11-17'])} if 'aec-utr-gaskan-hanbra-2026-11-17' in p else 'no utr')
for s in ['aec-cs2-all-nvo-2026-11-14','aec-nba-det-mia-2026-11-14']: print(s,p.get(s)); print(out.get(s,{}).get('fills'))
print([s for s in p if 'utr' in s or 'gaskan' in s][:5], [x for x in d if 'gaskan' in x['slug']][:3])
