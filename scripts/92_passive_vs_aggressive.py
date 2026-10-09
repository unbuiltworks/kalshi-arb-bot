import pandas as pd, numpy as np, csv, glob, gzip, json, sys, random, statistics
S=sys.argv[1]
bk=pd.concat([pd.read_parquet(f'{S}/data/zenodo_22084977/updown_15m.parquet'),
              pd.read_parquet(f'{S}/data/zenodo_22084977/updown_5m.parquet')],ignore_index=True)
bk['ts']=bk['captured_at'].astype('int64')//10**6   # dtype is datetime64[us, UTC]
bk=bk[bk['best_ask'].notna() & bk['best_bid'].notna() & (~bk['crossed'])]
idx={}
for (s,o),g in bk.groupby(['market_slug','outcome']):
    g=g.sort_values('ts'); idx[(s,o)]=(g['ts'].values,g['best_bid'].values,g['best_ask'].values)
rows=[]; nomatch=0
for p in sorted(glob.glob('export/package/dataset/fills/l5_fills_enriched_part*.csv.gz')):
    with gzip.open(p,'rt',newline='') as fh:
        for r in csv.DictReader(fh):
            k=(r['event_slug'],'Up' if r['leg']=='UP' else 'Down')
            if k not in idx: continue
            ts,bb,ba=idx[k]; t=int(r['timestamp'])
            j=np.searchsorted(ts,t,side='right')-1
            if j<0 or t-ts[j]>30: nomatch+=1; continue
            try: px=float(r['price'])
            except: continue
            rows.append((px,float(bb[j]),float(ba[j])))
print('matched L5 fills:',len(rows),' unmatched (no snapshot within 30s):',nomatch)
if not rows: sys.exit()
px=np.array([r[0] for r in rows]); bid=np.array([r[1] for r in rows]); ask=np.array([r[2] for r in rows])
tol=0.005
res={
 'matched_fills':len(rows),
 'mean_L5_fill_price':round(float(px.mean()),4),
 'mean_prevailing_best_bid':round(float(bid.mean()),4),
 'mean_prevailing_best_ask':round(float(ask.mean()),4),
 'mean_prevailing_mid':round(float(((bid+ask)/2).mean()),4),
 'at_or_below_bid_PASSIVE':round(float((px<=bid+tol).mean()),4),
 'inside_spread':round(float(((px>bid+tol)&(px<ask-tol)).mean()),4),
 'at_or_above_ask_AGGRESSIVE':round(float((px>=ask-tol).mean()),4),
 'mean_fill_minus_ask':round(float((px-ask).mean()),4),
 'mean_fill_minus_bid':round(float((px-bid).mean()),4),
 'mean_fill_minus_mid':round(float((px-(bid+ask)/2).mean()),4),
}
rng=random.Random(20261009)
d=(px-(bid+ask)/2).tolist(); k=len(d); m=min(k,20000)
bs=sorted(statistics.fmean([d[rng.randrange(k)] for _ in range(m)]) for _ in range(2000))
res['mean_fill_minus_mid_ci95']=[round(bs[50],4),round(bs[1949],4)]
print(json.dumps(res,indent=1))
json.dump(res,open('export/package/external_review/PASSIVE_VS_AGGRESSIVE_RESULT.json','w'),indent=1)
