"""Campaign report for the PMUS sports bot from exported production logs.

Usage: python3 campaign_report.py EVENTS.jsonl START_ISO END_ISO [OUT.json]
EVENTS.jsonl: one {"ts", "m"} row per PMUS_TWO_SIDED log line (the
scratchpad ingest format; L5_LIVE_HB lines are never stored).

Reports the owner's campaign measures (27 Nov): qualifying resting time,
capital utilization, gate outcomes, reward qualification, same-side
additions and acquisition costs, settled trading P&L and confirmed reward
payments. Every figure is labelled; modelled rewards are never realized.
"""
import json,collections,sys
from datetime import datetime,timezone
def pt(s): return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
EV=sys.argv[1]; W0=pt(sys.argv[2]); W1=pt(sys.argv[3])
OUT=sys.argv[4] if len(sys.argv)>4 else None
END=W1
ev=[]
for l in open(EV):
    r=json.loads(l); j=json.loads(r['m'].split(' ',1)[1]); j['_t']=pt(j['t']) if 't' in j else pt(r['ts'][:26]+'Z')
    ev.append(j)
ev.sort(key=lambda j:j['_t'])
allev=ev
ev=[j for j in ev if W0<=j['_t']<END]
by=collections.defaultdict(list)
for j in ev: by[j['kind']].append(j)
out={'window':{'start':sys.argv[2],'end_used':datetime.fromtimestamp(END,timezone.utc).isoformat(),'counts_by_kind':{k:len(v) for k,v in sorted(by.items())}}}
F=float
# 1 qualifying
hb=by['heartbeat']
def ph(h,p,f): return F(h['phases'][p][f])
q={}
for p in ('live','pregame'):
  for f in ('qualifying_contract_s','resting_contract_s','orders','fills'):
    tot=0;dec=0
    for a,b in zip(hb,hb[1:]):
      d=ph(b,p,f)-ph(a,p,f)
      if d>=0: tot+=d
      else: dec+=1; tot+=ph(b,p,f)
    q[f'{p}.{f}']=round(tot,3); q[f'{p}.{f}.decreases']=dec
# minute buckets
nmin=int((END-W0)//60)
mins_rest=set(); mins_qual=set(); mins_hb=set()
for h in hb: 
  m=int((h['_t']-W0)//60); mins_hb.add(m)
  if F(h['capital']['resting_orders_usd'])>0: mins_rest.add(m)
for a,b in zip(hb,hb[1:]):
  if sum(ph(b,p,'qualifying_contract_s')-ph(a,p,'qualifying_contract_s') for p in ('live','pregame'))>0:
    mins_qual.add(int((b['_t']-W0)//60))
q['window_minutes']=nmin; q['minutes_with_heartbeat']=len(mins_hb)
q['pct_minutes_resting_orders_gt0']=round(100*len(mins_rest)/nmin,1)
q['pct_minutes_qualifying_increased']=round(100*len(mins_qual)/nmin,1)
q['heartbeats']=len(hb); q['first_hb']=hb[0]['t']; q['last_hb']=hb[-1]['t']
out['1_qualifying']=q
# 2 capital
tot=0;tw=0;mx=0;zero_s=0;caps=[]
for i,h in enumerate(hb):
  c=h['capital']; v=F(c['resting_orders_usd'])+F(c['unpaired_inventory_usd'])+F(c['paired_awaiting_settlement_usd'])
  caps.append(v)
  t0=max(h['_t'],W0); t1=hb[i+1]['_t'] if i+1<len(hb) else END
  dt=max(0,t1-t0); tot+=v*dt; tw+=dt; mx=max(mx,v)
  if v==0: zero_s+=dt
mean=tot/tw
out['2_capital']={'time_weighted_mean_usd':round(mean,4),'max_usd':round(mx,4),'mean_pct_of_60_cap':round(100*mean/60,2),'max_pct_of_60_cap':round(100*mx/60,2),
 'mean_pct_of_20_at_risk':round(100*mean/20,2),'max_pct_of_20_at_risk':round(100*mx/20,2),'minutes_zero_capital':round(zero_s/60,1),'heartbeats_zero_capital':sum(1 for v in caps if v==0),
 'max_resting_orders_usd':max(F(h['capital']['resting_orders_usd']) for h in hb),'max_unpaired_usd':max(F(h['capital']['unpaired_inventory_usd']) for h in hb),'max_paired_awaiting_usd':max(F(h['capital']['paired_awaiting_settlement_usd']) for h in hb),
 'capacity_usd_min':min((F(h['account_limit']['capacity_usd']) for h in hb if h['account_limit'].get('capacity_usd') is not None),default=None),'capacity_usd_max':max((F(h['account_limit']['capacity_usd']) for h in hb if h['account_limit'].get('capacity_usd') is not None),default=None),'capacity_null_heartbeats':sum(1 for h in hb if h['account_limit'].get('capacity_usd') is None),'account_limit_reasons':dict(collections.Counter(str(h['account_limit'].get('reason')) for h in hb)),
 'submission_lock_states':dict(collections.Counter(json.dumps(h.get('submission_lock'),sort_keys=True) for h in hb)),
 'safety_latch_states':dict(collections.Counter(json.dumps(h.get('safety_latch')) for h in hb)),
 'active_len_dist':dict(collections.Counter(len(h.get('active') or []) for h in hb))}
# 3 gates
quotes=collections.defaultdict(list)
for j in by['RN1_QUOTE']: quotes[j['slug']].append(j)
touch=by['RN1_TOUCH_EV']
hard=collections.Counter(str(t.get('hard')) for t in touch)
sidewhy=collections.Counter(f"{s}:{t['sides'][s].get('why')}" for t in touch for s in ('YES','NO') if s in (t.get('sides') or {}))
gate_ev=collections.Counter(); gate_slugs=collections.defaultdict(set); nomatch=0
me_zero=0; me_total=0; inc_why=collections.Counter(); inc_proj=[]
elig=[t for t in touch if (t.get('net_usd_per_hour') or 0)>0]
for t in elig:
  ql=quotes.get(t['slug'],[])
  after=[x for x in ql if t['_t']<=x['_t']<=t['_t']+2]
  q0=after[0] if after else (max([x for x in ql if x['_t']<=t['_t']],key=lambda x:x['_t'],default=None))
  if q0 is None:
    # search outside window too
    ql2=[x for x in allev if x['kind']=='RN1_QUOTE' and x['slug']==t['slug'] and x['_t']<=t['_t']]
    q0=ql2[-1] if ql2 else None
  if q0 is None: nomatch+=1; gate_ev['no_quote_found']+=1; gate_slugs['no_quote_found'].add(t['slug']); continue
  match='after' if after else 'before'
  for s in ('YES','NO'):
    want=(q0.get('want') or {}).get(s)
    if want: g='rested'
    elif q0.get('block'): g='block:'+str(q0['block'])
    else: g='why:'+str((q0.get('why') or {}).get(s))
    gate_ev[g]+=1; gate_slugs[g].add(t['slug'])
    if 'marginal_ev' in g:
      me_total+=1
      tr=max((F((r or {}).get('reward_usd_per_hour') or 0) for r in t['sides'][s]['rows'].values()),default=0)
      mr=[m for m in (q0.get('marginal_ev') or []) if m.get('side')==s]
      if tr>0 and all(F(m.get('reward_usd_per_hour') or 0)==0 for m in mr): me_zero+=1
      inc=q0.get('incentive') or {}
      inc_why[str(inc.get('why'))]+=1; inc_proj.append(F(inc.get('projected_usd') or 0))
out['3_gates']={'touch_ev_events':len(touch),'touch_ev_eligible_net_gt0':len(elig),'eligible_distinct_slugs':len({t['slug'] for t in elig}),
 'side_outcomes_by_gate_events':dict(gate_ev.most_common()),'side_outcomes_by_gate_distinct_slugs':{k:len(v) for k,v in gate_slugs.items()},
 'marginal_ev_rejections':me_total,'marginal_ev_rej_with_quote_reward0_while_touch_reward_gt0':me_zero,
 'marginal_ev_rej_incentive_why':dict(inc_why),'marginal_ev_rej_projected_usd_max':max(inc_proj,default=None),'marginal_ev_rej_projected_usd_mean':round(sum(inc_proj)/len(inc_proj),4) if inc_proj else None,
 'touch_ev_by_hard':dict(hard.most_common()),'touch_ev_by_side_why':dict(sidewhy.most_common()),
 'all_quotes_block_counts':dict(collections.Counter(str(x.get('block')) for x in by['RN1_QUOTE']).most_common()),
 'all_quotes_why_counts':dict(collections.Counter(f"{s}:{(x.get('why') or {}).get(s)}" for x in by['RN1_QUOTE'] for s in ('YES','NO')).most_common(15)),
 'quotes_with_want':sum(1 for x in by['RN1_QUOTE'] if any((x.get('want') or {}).values()))}
# 4 reward qualification
cnt=collections.Counter(); tim=collections.Counter(); mxp=0; mxslug=None
for slug,ql in quotes.items():
  for i,x in enumerate(ql):
    w=str((x.get('incentive') or {}).get('why'))
    nxt=ql[i+1]['_t'] if i+1<len(ql) else END
    cnt[w]+=1; tim[w]+=min(120,max(0,nxt-x['_t']))
    p=F((x.get('incentive') or {}).get('projected_usd') or 0)
    if p>mxp: mxp=p; mxslug=slug
tt=sum(tim.values())
out['4_reward_qual']={'by_count':dict(cnt),'by_time_s':{k:round(v) for k,v in tim.items()},'by_time_pct':{k:round(100*v/tt,1) for k,v in tim.items()},'max_projected_usd':mxp,'max_projected_slug':mxslug,
 'min_payout_usd_values':sorted({str((x.get('incentive') or {}).get('min_payout_usd')) for x in by['RN1_QUOTE']})}
# 5 payments: latest overall (in/after window)
def latest(k): 
  l=[j for j in allev if j['kind']==k and j['_t']>=W0]; return l[-1] if l else None
rc=latest('REWARD_CHECK'); ce=latest('CAMPAIGN_ECONOMICS'); pp=latest('PILOT_PNL'); cal=latest('REWARD_CALIBRATION')
pay={}
if rc:
  rows=rc.get('rows_brief') or []
  pay['reward_check_t']=rc['t']; pay['credited_usd']=rc.get('credited_usd'); pay['accrued_not_credited_usd']=rc.get('accrued_not_credited_usd'); pay['by_status']=rc.get('by_status')
  pay['rows_brief_n']=len(rows); d0=datetime.fromtimestamp(W0,timezone.utc).strftime('%Y-%m-%d'); pay['rows_dated_campaign_day']=[r for r in rows if str(r.get('date'))==d0]
  pay['reward_check_count_in_window']=len(by['REWARD_CHECK'])
if ce:
  c=ce['campaign']; pay['campaign_economics_t']=ce['t']; pay['campaign']={k:c.get(k) for k in ('since','until','markets','fills','contracts','spend_usd','completed_pairs','pair_edge_usd','residual_contracts','fees_charged_usd','rewards_paid_usd','rewards_pending_usd','rewards_skipped_usd','net_realized_usd','realized_cash_basis_usd')}
if pp: pay['pilot_pnl_t']=pp['t']; pay['pilot_realized_net_usd']=pp.get('realized_net_usd'); pay['pilot_status']=pp.get('status'); pay['pilot_execution']=pp.get('execution')
if cal: pay['reward_calibration_t']=cal['t']; pay['reward_calibration_brief']=json.dumps(cal)[:600]
out['5_payments']=pay
# 6 orders/fills
o=collections.Counter(j['slug'] for j in by['ORDER']); f=collections.Counter(j['slug'] for j in by['FILL'])
ons=collections.Counter(f"{j.get('stage')}:{j.get('why')}" for j in by['ORDER_NOT_SENT'])
out['6_orders_fills']={'orders':len(by['ORDER']),'fills':len(by['FILL']),'orders_by_slug':dict(o),'fills_by_slug':dict(f),
 'order_stage':dict(collections.Counter(j.get('stage') for j in by['ORDER'])),'fill_contracts':sum(F(j['qty']) for j in by['FILL']),
 'order_not_sent':len(by['ORDER_NOT_SENT']),'order_not_sent_by_stage_why':dict(ons),
 'selected':len(by['SELECTED']),'reselected':len(by['RESELECTED']),'fill_markout':len(by['FILL_MARKOUT'])}
# 7 same-side additions and acquisition costs (venue-reported fills;
# classified by the position BEFORE each fill)
orders={j.get('order_id'):j for j in by['ORDER'] if j.get('order_id')}
cls=collections.defaultdict(lambda:{'fills':0,'contracts':0.0,'cost_usd':0.0})
rows7=[]
for j in by['FILL']:
  y,n=F(j['pos']['YES']),F(j['pos']['NO']); q=F(j['qty']); px=F(j['px'])
  if j['side']=='YES': y-=q
  else: n-=q
  me,ot=(y,n) if j['side']=='YES' else (n,y)
  if me==0 and ot==0: c='opening'
  elif me>=ot: c='same_side_addition'
  else: c='completion' if q<=ot-me+1e-9 else 'completion_and_flip'
  k=cls[c]; k['fills']+=1; k['contracts']+=q; k['cost_usd']+=px*q
  rows7.append({'t':j['t'],'slug':j['slug'],'side':j['side'],'px':j['px'],'qty':j['qty'],'class':c,'stage':(orders.get(j.get('order_id')) or {}).get('stage'),'phase':j.get('phase')})
for k in cls.values(): k['avg_px']=round(k['cost_usd']/k['contracts'],4) if k['contracts'] else None
mk=collections.defaultdict(list)
for j in by['FILL_MARKOUT']: mk[j.get('slug')].append(j)
out['7_accumulation']={'by_class':{c:dict(v,cost_usd=round(v['cost_usd'],4)) for c,v in cls.items()},'fills':rows7,
 'markout_events':len(by['FILL_MARKOUT']),'label':'acquisition cost = venue fill price; markout is a mark, not profit'}
if OUT: json.dump(out,open(OUT,'w'),indent=1,default=str)
print(json.dumps(out,indent=1,default=str))
