import json,subprocess,urllib.parse,time,collections,sys,datetime as dt
G="https://gateway.polymarket.us"
def get(p):
    for i in range(3):
        r=subprocess.run(["curl","-sS","--max-time","30",G+p],capture_output=True,text=True)
        try: return json.loads(r.stdout)
        except Exception: time.sleep(1)
    return {}
now=time.time()
evs=[];off=0
while off<6000:
    d=get(f"/v1/events?active=true&closed=false&categories=sports&limit=500&offset={off}")
    e=d.get('events') if isinstance(d,dict) else d
    if not e: break
    evs+=e; off+=500; time.sleep(0.5)
def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace('Z','+00:00').replace(' ','T')).timestamp()
    except Exception: return None
mk=[]
for e in evs:
    for m in e.get('markets') or []:
        if m.get('closed') or not m.get('active'): continue
        gs=ts(m.get('gameStartTime') or e.get('startTime') or e.get('startDate'))
        if gs is None or gs>now+6*3600 or gs<now-6*3600: continue
        mk.append({'slug':m['slug'],'type':m.get('sportsMarketType'),'gs':gs,'live':e.get('live'),'tick':m.get('orderPriceMinTickSize')})
print('events',len(evs),'markets in +-6h',len(mk),file=sys.stderr)
found={}
sl=[m['slug'] for m in mk]
for i in range(0,len(sl),40):
    d=get("/v1/incentives?"+"&".join("symbols="+urllib.parse.quote(s,safe='') for s in sl[i:i+40]))
    for p in d.get('programs') or []: found[p['marketSlug']]=p
    time.sleep(0.5)
print('with programmes',len(found),file=sys.stderr)
out=[]
for m in mk:
    p=found.get(m['slug'])
    if not p: continue
    live = m['gs']<=now
    per='live' if live else ('day_of' if m['gs']-now<=6*3600 else 'early')
    tps=[t for t in p['timePeriods'] if t.get('status')=='active' and t.get('period')==per and t.get('programType')=='liquidityProgram']
    if not tps: continue
    tp=tps[0]
    b=get(f"/v1/markets/{m['slug']}/book"); time.sleep(0.5)
    md=b.get('marketData') or b
    m.update(period=per,pool=tp['rewardPool'],df=tp['discountFactor'],target=tp['targetSize'],programId=tp['programId'],book={'bids':md.get('bids'),'offers':md.get('offers') or md.get('asks')})
    out.append(m)
json.dump({'t':now,'markets':out},open(sys.argv[1],'w'))
print('measured',len(out),file=sys.stderr)
