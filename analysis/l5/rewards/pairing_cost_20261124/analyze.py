import json,gzip,collections,os,random,math
from datetime import datetime
P=os.path.dirname(os.path.abspath(__file__))
RA=P+'/../residual_audit/'
R='/home/user/kalshi-arb-bot/analysis/l5/'
def ts(t):
    return datetime.fromisoformat(t.replace('Z','+00:00')).timestamp() if t else None
def yes_settle(path):
    m=json.load(open(path))['market']
    op=m['outcomePrices']; op=json.loads(op) if isinstance(op,str) else op
    v=float(op[0]); lg=[s for s in m['marketSides'] if s.get('long')]
    assert v in (0.0,1.0) and float(lg[0]['price'])==v, path
    return v
# ---------- period (i)
d=json.load(gzip.open(R+'rewards/sports_orders_fills_20261114_20261118.json.gz'))
stage={x['order_id']:x['stage'] for x in d if x['kind']=='ORDER' and 'side' in x}
F1=[]
for x in sorted([x for x in d if x['kind']=='FILL'],key=lambda x:x['t']):
    F1.append(dict(slug=x['slug'],t=x['t'],side=x['side'],px=float(x['px']),qty=float(x['qty']),stage=stage.get(x['order_id'],'?'),pos=x['pos']))
S1={s:yes_settle(RA+'api/'+s+'.json') for s in {f['slug'] for f in F1}}
# ---------- period (ii)
F2=[]
for r in json.load(open(R+'sports/adverse/fills_context.json')):
    F2.append(dict(slug=r['slug'],t=r['fill_time'],side=r['side'],px=float(r['px']),qty=float(r['qty']),stage=r['stage']))
B='atc-sea-bol-udi-2026-11-22-bol';G='atc-sea-sas-gen-2026-11-22-gen'
for s,t,sd,px,st in [(B,'14:09:27','NO',.47,'log:open'),(G,'14:12:31','NO',.66,'log:open'),(B,'14:14:33','YES',.49,'log:complete'),
    (B,'14:24:26','YES',.45,'log:extend'),(B,'14:28:39','YES',.43,'log:extend'),(G,'14:28:43','NO',.68,'log:extend'),
    (B,'14:40:52','NO',.51,'log:complete'),(B,'14:44:08','NO',.51,'log:complete')]:
    F2.append(dict(slug=s,t='2026-11-22T%s+00:00'%t,side=sd,px=px,qty=2.0,stage=st))
C='aec-atp-cescru-joagui-2026-11-23'
F2+= [dict(slug=C,t='2026-11-24T02:14:09+00:00',side='YES',px=.31,qty=1.0,stage='log:open'),
      dict(slug=C,t='2026-11-24T02:14:54+00:00',side='NO',px=.66,qty=1.0,stage='log:complete')]
# 24 Nov 17:00-18:50Z fills: prices/order given by the caller, exact times not on file (seq only)
for i,(s,sd,px,st) in enumerate([('aec-atp-matsca-wisabd-2026-11-24','YES',.55,'given:open'),('aec-atp-matsca-wisabd-2026-11-24','NO',.39,'given:complete'),
    ('aec-atp-juaoso-olibon-2026-11-23','YES',.57,'given:open'),('aec-atp-juaoso-olibon-2026-11-23','NO',.39,'given:complete'),
    ('aec-atp-juagom-bengeo-2026-11-23','NO',.44,'given:open'),('aec-atp-samher-yanale-2026-11-23','NO',.61,'given:open')]):
    F2.append(dict(slug=s,t=None,seq=i,side=sd,px=px,qty=1.0,stage=st))
S2={s:yes_settle(P+'/api2/'+s+'.json') for s in {f['slug'] for f in F2}}

def sport(s): return s.split('-')[1]
def cbucket(c): return '<=0.97' if c<=0.97+1e-9 else ('0.97-1.00' if c<=1.0+1e-9 else '>1.00')
def tbucket(dt): return 'unknown' if dt is None else ('<1min' if dt<60 else ('1-10min' if dt<600 else '>10min'))
def A(): return collections.defaultdict(float)

def run(F,S,name):
    bym=collections.defaultdict(list)
    for i,f in enumerate(F): f['i']=i; bym[f['slug']].append(f)
    pieces=[]   # adding pieces (lots) and reducing pieces
    lots_all=[]; matches=[]; poschk=0
    for s,fl in bym.items():
        y=n=0.0; lots=[]  # open lots of held side FIFO: dict
        for f in fl:
            q=f['qty']; val=S[s] if f['side']=='YES' else 1-S[s]; f['val']=val
            net=y-n; held='YES' if net>1e-9 else ('NO' if net<-1e-9 else None)
            red=min(q,abs(net)) if (held and f['side']!=held) else 0.0
            add=q-red
            f['red']=red; f['add']=add
            f['cls']='REDUCING' if add<1e-9 else ('ADDING' if red<1e-9 else 'SPLIT')
            r=red
            while r>1e-9:
                L=lots[0]; m=min(r,L['rem']); L['rem']-=m; r-=m
                dt=(ts(f['t'])-ts(L['t'])) if (f['t'] and L['t']) else None
                matches.append(dict(slug=s,qty=m,open_px=L['px'],comp_px=f['px'],pair_cost=L['px']+f['px'],dt=dt,held_val=L['val'],
                                    open_stage=L['stage'],comp_stage=f['stage'],sport=sport(s)))
                if L['rem']<1e-9: lots.pop(0)
            if add>1e-9:
                L=dict(slug=s,t=f['t'],px=f['px'],qty=add,rem=add,val=val,stage=f['stage'],side=f['side'],sport=sport(s)); lots.append(L); lots_all.append(L)
            if f['side']=='YES': y+=q
            else: n+=q
            if 'pos' in f:
                if abs(float(f['pos']['YES'])-y)>1e-6 or abs(float(f['pos']['NO'])-n)>1e-6: poschk+=1
    out=dict(name=name,n_fills=len(F),n_markets=len(bym),pos_mismatch_vs_logged=poschk)
    # Q1
    q1=collections.defaultdict(A); st=collections.defaultdict(A)
    for f in F:
        for cls,q in (('ADDING',f['add']),('REDUCING',f['red'])):
            if q>1e-9:
                a=q1[cls]; a['contracts']+=q; a['dollars']+=q*f['px']; a['fill_pieces']+=1
                a['hts_pnl_standalone']+=q*(f['val']-f['px'])
                b=st[f['stage']+' | '+cls]; b['contracts']+=q; b['dollars']+=q*f['px']; b['fill_pieces']+=1
        q1['fills_'+f['cls']]['fills']+=1
    out['q1_class']={k:dict(v) for k,v in q1.items()}; out['q1_stage_x_class']={k:dict(v) for k,v in sorted(st.items())}
    # Q2
    def hts(lots):
        q=sum(l['qty'] for l in lots); c=sum(l['qty']*l['px'] for l in lots); pay=sum(l['qty']*l['val'] for l in lots)
        if q<1e-9: return dict(contracts=0)
        wr=pay/q; avg=c/q; wc=sum(l['qty']*l['px'] for l in lots if l['val']==1)
        mk=collections.defaultdict(float)
        for l in lots: mk[l['slug']]+=l['qty']*(l['val']-l['px'])
        v=list(mk.values()); nm=len(v); mean=sum(v)/nm
        sd=math.sqrt(sum((x-mean)**2 for x in v)/(nm-1)) if nm>1 else float('nan')
        se_tot=sd*math.sqrt(nm)
        # per-lot variance assuming independent contracts at price p
        se_ind=math.sqrt(sum(l['qty']*l['px']*(1-l['px']) for l in lots))
        return dict(contracts=q,lots=len(lots),markets=nm,cost=c,payout=pay,pnl=pay-c,avg_px=avg,win_rate_contracts=wr,win_rate_dollars=wc/c,
                    pnl_per_contract=(pay-c)/q,binom_se_winrate=math.sqrt(avg*(1-avg)/q),z_binom_indep=(wr-avg)/math.sqrt(avg*(1-avg)/q),
                    se_pnl_if_contracts_independent=se_ind,markets_pos=sum(1 for x in v if x>1e-9),markets_neg=sum(1 for x in v if x<-1e-9),
                    mean_pnl_per_market=mean,sd_pnl_per_market=sd,se_total_pnl_market_clustered=se_tot,
                    t_market_clustered=(pay-c)/se_tot if se_tot==se_tot and se_tot>0 else None,_mk=mk)
    h=hts(lots_all); mk_h=h.pop('_mk'); out['q2_hold_to_settlement']=h
    # Q3 actual
    tot=sum(f['qty']*(f['val']-f['px']) for f in F)
    pq=sum(m['qty'] for m in matches); pc=sum(m['qty']*m['pair_cost'] for m in matches)
    res=[l for l in lots_all if l['rem']>1e-9]
    rq=sum(l['rem'] for l in res); rc=sum(l['rem']*l['px'] for l in res); rpay=sum(l['rem']*l['val'] for l in res)
    # average-cost convention (as in the reconciliation)
    ac_pair=ac_res=0; ac_resn=0; ac_cost=0
    for s,fl in bym.items():
        y=sum(f['qty'] for f in fl if f['side']=='YES'); yc=sum(f['qty']*f['px'] for f in fl if f['side']=='YES')
        n=sum(f['qty'] for f in fl if f['side']=='NO'); nc=sum(f['qty']*f['px'] for f in fl if f['side']=='NO')
        pr=min(y,n); rqq=abs(y-n); rcc=0;rv=0
        if rqq>1e-9:
            rcc=rqq*(yc/y if y>n else nc/n); rv=rqq*(S[s] if y>n else 1-S[s]); ac_resn+=1
        ac_pair+=pr-(yc+nc-rcc); ac_res+=rv-rcc; ac_cost+=rcc
    comp_cost=sum(m['qty']*m['comp_px'] for m in matches)
    won=[m for m in matches if m['held_val']==1]; lost=[m for m in matches if m['held_val']==0]
    qw=sum(m['qty'] for m in won); ql=sum(m['qty'] for m in lost)
    out['q3_actual']=dict(total_all_fills_settled=tot,
        fifo=dict(pairs=pq,pair_cost_avg=pc/pq if pq else None,pair_pnl=pq-pc,residual_contracts=rq,residual_lots=len(res),residual_markets=len({l['slug'] for l in res}),
                  residual_cost=rc,residual_payout=rpay,residual_pnl=rpay-rc,residual_won_contracts=rpay,total=pq-pc+rpay-rc),
        avg_cost_convention=dict(pair_pnl=ac_pair,residual_pnl=ac_res,residual_cost=ac_cost,residual_markets=ac_resn,total=ac_pair+ac_res),
        hold_pnl=h['pnl'],net_effect_of_completions=tot-h['pnl'],
        decomposition=dict(completion_contracts=pq,completion_cost=comp_cost,avg_completion_px=comp_cost/pq if pq else None,
            completion_payout_at_settlement=ql, # completion contract pays when held leg lost
            paired_legs_won_contracts=qw,paired_legs_lost_contracts=ql,share_paired_legs_won=qw/pq if pq else None,
            paired_legs_won_dollars_share=(sum(m['qty']*m['open_px'] for m in won)/sum(m['qty']*m['open_px'] for m in matches)) if pq else None,
            avg_open_px_paired=sum(m['qty']*m['open_px'] for m in matches)/pq if pq else None,
            upside_given_up_on_winners=-sum(m['qty']*m['comp_px'] for m in won),   # paid c, got nothing extra
            loss_avoided_on_losers=sum(m['qty']*(1-m['comp_px']) for m in lost),
            avg_comp_px_when_leg_won=(sum(m['qty']*m['comp_px'] for m in won)/qw) if qw else None,
            avg_comp_px_when_leg_lost=(sum(m['qty']*m['comp_px'] for m in lost)/ql) if ql else None,
            paired_legs_hold_pnl=sum(m['qty']*(m['held_val']-m['open_px']) for m in matches),
            paired_legs_actual_pnl=pq-pc,
            unpaired_legs_won_share=(rpay/rq) if rq else None,
            breakeven_share_of_paired_legs_losing=comp_cost/pq if pq else None,
            observed_share_of_paired_legs_losing=ql/pq if pq else None,
            binom_se_share=math.sqrt((comp_cost/pq)*(1-comp_cost/pq)/pq) if pq else None))
    # market-clustered uncertainty of the completion effect + bootstrap
    mk_c=collections.defaultdict(float)
    for m in matches: mk_c[m['slug']]+=m['qty']*((1-m['held_val'])-m['comp_px'])
    slugs=list(bym); eff=[mk_c.get(s,0.0) for s in slugs]; hh=[mk_h.get(s,0.0) for s in slugs]
    random.seed(7); be=[];bh=[];ba=[]
    for _ in range(20000):
        ix=[random.randrange(len(slugs)) for _ in slugs]
        e=sum(eff[i] for i in ix); x=sum(hh[i] for i in ix); be.append(e); bh.append(x); ba.append(e+x)
    def ci(v): v=sorted(v); return [v[int(.025*len(v))],v[int(.975*len(v))]]
    def sd(v): m=sum(v)/len(v); return math.sqrt(sum((x-m)**2 for x in v)/(len(v)-1))
    out['uncertainty_market_bootstrap']=dict(markets=len(slugs),completion_effect=dict(point=sum(eff),se=sd(be),ci95=ci(be),share_boot_positive=sum(1 for x in be if x>0)/len(be),
            markets_with_completions=len(mk_c),markets_effect_pos=sum(1 for x in eff if x>1e-9),markets_effect_neg=sum(1 for x in eff if x<-1e-9)),
        hold_pnl=dict(point=sum(hh),se=sd(bh),ci95=ci(bh)),actual_pnl=dict(point=sum(eff)+sum(hh),se=sd(ba),ci95=ci(ba)))
    # Q4 splits on paired lots + residual
    def agg(ms):
        q=sum(m['qty'] for m in ms)
        if q<1e-9: return dict(contracts=0)
        w=sum(m['qty'] for m in ms if m['held_val']==1)
        return dict(pairs=q,markets=len({m['slug'] for m in ms}),avg_open_px=sum(m['qty']*m['open_px'] for m in ms)/q,avg_comp_px=sum(m['qty']*m['comp_px'] for m in ms)/q,
            avg_pair_cost=sum(m['qty']*m['pair_cost'] for m in ms)/q,actual_pair_pnl=sum(m['qty']*(1-m['pair_cost']) for m in ms),
            hold_pnl_of_open_legs=sum(m['qty']*(m['held_val']-m['open_px']) for m in ms),
            completion_effect=sum(m['qty']*((1-m['held_val'])-m['comp_px']) for m in ms),held_leg_win_share=w/q,breakeven_lose_share=sum(m['qty']*m['comp_px'] for m in ms)/q)
    resm=[dict(slug=l['slug'],qty=l['rem'],open_px=l['px'],val=l['val']) for l in res]
    def aggres(rs):
        q=sum(r['qty'] for r in rs)
        if q<1e-9: return dict(contracts=0)
        return dict(contracts=q,markets=len({r['slug'] for r in rs}),avg_px=sum(r['qty']*r['open_px'] for r in rs)/q,pnl=sum(r['qty']*(r['val']-r['open_px']) for r in rs),win_share=sum(r['qty']*r['val'] for r in rs)/q)
    q4={}
    q4['by_pair_cost']={k:agg([m for m in matches if cbucket(m['pair_cost'])==k]) for k in ['<=0.97','0.97-1.00','>1.00']}
    q4['by_time_to_complete']={k:agg([m for m in matches if tbucket(m['dt'])==k]) for k in ['<1min','1-10min','>10min','unknown']}
    q4['by_completion_stage']={k:agg([m for m in matches if m['comp_stage']==k]) for k in sorted({m['comp_stage'] for m in matches})}
    q4['never_completed_residual']=aggres(resm)
    bs={}
    for sp in sorted({sport(s) for s in bym}):
        ls=[l for l in lots_all if l['sport']==sp]; hs=hts(ls); hs.pop('_mk',None)
        ms=[m for m in matches if m['sport']==sp]; rs=[r for r in resm if sport(r['slug'])==sp]
        act=sum(f['qty']*(f['val']-f['px']) for f in F if sport(f['slug'])==sp)
        bs[sp]=dict(markets=len({s for s in bym if sport(s)==sp}),hold={k:hs.get(k) for k in ['contracts','cost','payout','pnl','avg_px','win_rate_contracts','pnl_per_contract','markets']},
                    actual_total=act,completion_effect=act-(hs.get('pnl') or 0),paired=agg(ms),residual=aggres(rs))
    q4['by_sport']=bs
    out['q4_splits']=q4
    # by day
    bd={}
    for day in sorted({f['t'][:10] if f['t'] else '2026-11-24' for f in F}):
        ff=[f for f in F if (f['t'][:10] if f['t'] else '2026-11-24')==day]
        bd[day]=dict(add_contracts=sum(f['add'] for f in ff),hold_pnl=sum(f['add']*(f['val']-f['px']) for f in ff),
                     red_contracts=sum(f['red'] for f in ff),completion_effect=sum(f['red']*(f['val']-f['px']) for f in ff))
    out['by_fill_day']=bd
    # opening price buckets (calibration)
    cal={}
    for lo,hi in [(0,.3),(.3,.45),(.45,.55),(.55,.7),(.7,1.01)]:
        ls=[l for l in lots_all if lo<=l['px']<hi]; q=sum(l['qty'] for l in ls)
        if q: cal['%.2f-%.2f'%(lo,hi)]=dict(contracts=q,avg_px=sum(l['qty']*l['px'] for l in ls)/q,win_rate=sum(l['qty']*l['val'] for l in ls)/q,pnl=sum(l['qty']*(l['val']-l['px']) for l in ls))
    out['hold_by_open_price']=cal
    return out
res={'period_i_14_18_nov':run(F1,S1,'14-18 Nov'),'period_ii_22_24_nov':run(F2,S2,'22-24 Nov bounded campaigns')}
res['period_ii_22_24_nov']['fills']=[{k:f[k] for k in ['slug','t','side','px','qty','stage','cls','val']} for f in F2]
res['period_ii_22_24_nov']['yes_settle']=S2
json.dump(res,open(P+'/result.json','w'),indent=1)
print(json.dumps(res,indent=1))
