import sys, csv, gzip, glob, os, math, collections, json
sys.path.insert(0,'/home/user/kalshi-arb-bot/research/l5_forensic_export')
import importlib.util
spec=importlib.util.spec_from_file_location("c2","/home/user/kalshi-arb-bot/research/l5_forensic_export/C2_twomode_and_prediction.py")
c2=importlib.util.module_from_spec(spec); spec.loader.exec_module(c2)
import numpy as np

DS='/home/user/kalshi-arb-bot/export/package/dataset'
LB='/home/user/kalshi-arb-bot/export/package/external_review/A1_fill_labels.csv.gz'
lab={}
for r in csv.DictReader(gzip.open(LB,'rt')):
    lab[(r['event_slug'],r['trade_seq'])]=r['classification']
wins=collections.defaultdict(list)
for p in sorted(glob.glob(os.path.join(DS,'fills','l5_fills_enriched_part*.csv.gz'))):
    for r in csv.DictReader(gzip.open(p,'rt')):
        wins[r['event_slug']].append(r)
f=c2.f
slugs=sorted(wins,key=lambda s:int(wins[s][0]['timestamp']))

def build(only_labelled):
    cut=None; rows=[]
    for slug in slugs:
        rr=sorted(wins[slug],key=lambda r:int(r['trade_seq']))
        for i in range(len(rr)-1):
            cur,nxt=rr[i],rr[i+1]
            c=lab.get((slug,cur['trade_seq']))
            if only_labelled and c not in ('TAKER_OBSERVED','MAKER_INFERRED'): continue
            y=int(nxt['leg_switched_from_prev'] or 0)
            rq=f(cur['residual_qty'],0.0) or 0.0; rv=f(cur['residual_vwap']) or 0.5
            tts=f(cur['seconds_to_settlement']) or 0.0; dur=f(cur['duration_minutes']) or 15.0
            px=f(cur['price']) or 0.5; sz=f(cur['size']) or 0.0
            istk=1.0 if c=='TAKER_OBSERVED' else 0.0
            rows.append((int(cur['timestamp']),y,math.log1p(rq),rv,tts/(dur*60.0),
                         1.0 if cur['leg']=='UP' else 0.0,istk,math.log1p(sz),px))
    rows.sort(key=lambda x:x[0])
    k=int(len(rows)*0.7)
    return rows[:k],rows[k:]

def run(tr,te,cols,name):
    X=lambda R:[[1.0]+[r[c] for c in cols] for r in R]
    y=lambda R:[r[1] for r in R]
    m=c2.logistic(X(tr),y(tr)); ps=c2.predict_all(m,X(te))
    return {'model':name,'n_train':len(tr),'n_test':len(te),
            'oos_logloss':round(c2.logloss(ps,y(te)),6),
            'oos_auc':round(c2.auc(ps,y(te)),5)}

INV=[2,3,4,5]; ISTK=[6]; SZPX=[7,8]
out={}
tr,te=build(False)
out['FULL_CORPUS']={
 'M0_constant':run(tr,te,[],'M0'),
 'M1_inventory_clock':run(tr,te,INV,'M1'),
 'M2a_plus_size_price_NO_istk':run(tr,te,INV+SZPX,'M2a'),
 'M2b_plus_istk_only':run(tr,te,INV+ISTK,'M2b'),
 'M2_full':run(tr,te,INV+ISTK+SZPX,'M2'),
}
tr2,te2=build(True)
out['LABELLED_WINDOW_ONLY']={
 '_note':'only fills with a real TAKER_OBSERVED/MAKER_INFERRED label, so is_taker is never a proxy for "outside the label window"',
 'M0_constant':run(tr2,te2,[],'M0'),
 'M1_inventory_clock':run(tr2,te2,INV,'M1'),
 'M2a_plus_size_price_NO_istk':run(tr2,te2,INV+SZPX,'M2a'),
 'M2b_plus_istk_only':run(tr2,te2,INV+ISTK,'M2b'),
 'M2_full':run(tr2,te2,INV+ISTK+SZPX,'M2'),
}
json.dump(out,open('/home/user/kalshi-arb-bot/export/package/external_review/C2b_PREDICTION_DECOMPOSITION.json','w'),indent=1)
for scope,d in out.items():
    print('===',scope)
    for k,v in d.items():
        if k.startswith('_'): continue
        print('  %-30s logloss %.6f  AUC %.5f  (n_test %d)'%(k,v['oos_logloss'],v['oos_auc'],v['n_test']))
