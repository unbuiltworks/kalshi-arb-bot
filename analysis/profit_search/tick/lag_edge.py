"""Does fresh spot predict the stale Kalshi book's next move?

Spot arrives at median 3.37 ms skew; the Kalshi book arrives at median 1,541 ms
after its own venue transact_time. So between a spot move and the book's next
observed state there is a real information window. Strictly causal: the signal
uses only spot with ev_ns <= the book's VENUE timestamp, and the target is the
next book snapshot.
"""
import json, bisect, statistics as st

spot=json.load(open('analysis/profit_search/tick/spot.json'))
book=json.load(open('analysis/profit_search/tick/book.json'))
sev=[r['ev_ns']/1e9 for r in spot]

def spot_at(t):
    i=bisect.bisect_right(sev,t)-1
    return spot[i]['px'] if i>=0 else None

def mid(r):
    if not r['bids'] or not r['offers']: return None
    return (r['bids'][0][0]+r['offers'][0][0])/2.0

rows=[]
for i in range(len(book)-1):
    a,b=book[i],book[i+1]
    if a['slug']!=b['slug']: continue
    if a['state']!='MARKET_STATE_OPEN': continue
    m0,m1=mid(a),mid(b)
    if m0 is None or m1 is None: continue
    vt=a['venue_t'] or a['t']
    # spot move over the 30s BEFORE the book's own venue timestamp (no lookahead)
    s_now=spot_at(vt); s_prev=spot_at(vt-30.0)
    # spot move the book had NOT yet seen: between its venue time and our receipt
    s_recv=spot_at(a['t'])
    if None in (s_now,s_prev,s_recv) or s_prev<=0: continue
    rows.append(dict(slug=a['slug'], t=a['t'],
                     ret30=(s_now-s_prev)/s_prev*1e4,          # bp
                     unseen=(s_recv-s_now)/s_now*1e4,          # bp the book hadn't absorbed
                     dmid=(m1-m0)*100.0,                       # cents
                     spread=(a['offers'][0][0]-a['bids'][0][0])*100.0,
                     dt=b['t']-a['t']))
print("paired book transitions: %d across %d windows"
      % (len(rows),len(set(r['slug'] for r in rows))))
if not rows: raise SystemExit
def corr(xs,ys):
    if len(xs)<10: return float('nan'),float('nan')
    mx,my=st.mean(xs),st.mean(ys); sx,sy=st.pstdev(xs),st.pstdev(ys)
    if sx<=0 or sy<=0: return float('nan'),float('nan')
    cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)
    return cov/(sx*sy), cov/(sx*sx)
print("\nmedian spread %.2f c   median |dmid| %.2f c   median book gap %.1fs"
      % (st.median([r['spread'] for r in rows]),
         st.median([abs(r['dmid']) for r in rows]), st.median([r['dt'] for r in rows])))
print("\n%-34s %6s %9s %11s %9s" % ("signal","n","corr","beta(c/bp)","hit rate"))
for lab,key in (("spot return, 30s pre-venue-ts","ret30"),
                ("spot move book had NOT seen","unseen")):
    xs=[r[key] for r in rows]; ys=[r['dmid'] for r in rows]
    c,b=corr(xs,ys)
    hit=sum(1 for x,y in zip(xs,ys) if x*y>0)/len(xs)
    print("%-34s %6d %9.4f %11.4f %9.3f" % (lab,len(xs),c,b,hit))
print("\nmagnitude of the unseen move (what a faster feed would be trading on):")
u=sorted(abs(r['unseen']) for r in rows)
print("  |unseen| bp: median %.2f  p75 %.2f  p90 %.2f  max %.2f"
      % (u[len(u)//2],u[3*len(u)//4],u[int(.9*len(u))],u[-1]))
print("  BTC at ~80,500: 1 bp = $8.05 of index move")
