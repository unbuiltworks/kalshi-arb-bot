"""Extract tick spot rows from persisted Railway log dumps of BTC_REF_ROWS."""
import json, sys, glob, os

def extract(paths):
    out=[]
    for p in paths:
        d=json.load(open(p))
        for e in d.get('deploy') or []:
            m=e.get('message') or ''
            i=m.find('BTC_REF_ROWS')
            if i<0: continue
            try: obj=json.loads(m[i+len('BTC_REF_ROWS'):])
            except Exception: continue
            for r in obj.get('rows') or []:
                k=r.get('kind')
                if k not in ('ref_bbo','ref_trade'): continue
                try:
                    out.append(dict(kind=k, ev_ns=int(r['ev_ns']), t_ns=int(r['t_ns']),
                                    skew_ns=int(r.get('skew_ns') or 0),
                                    px=float(r['px']),
                                    bid=float(r['bid']) if r.get('bid') else None,
                                    ask=float(r['ask']) if r.get('ask') else None,
                                    qty=float(r['qty']) if r.get('qty') else None,
                                    side=r.get('side')))
                except Exception: continue
    out.sort(key=lambda r:r['ev_ns'])
    return out

if __name__=="__main__":
    rows=extract(sys.argv[1:])
    import statistics as st, datetime as dt
    print("spot rows: %d" % len(rows))
    if not rows: sys.exit()
    t0,t1=rows[0]['ev_ns']/1e9, rows[-1]['ev_ns']/1e9
    print("span: %s -> %s  (%.1f min)" %
          (dt.datetime.fromtimestamp(t0,dt.UTC).strftime('%H:%M:%S'),
           dt.datetime.fromtimestamp(t1,dt.UTC).strftime('%H:%M:%S'),(t1-t0)/60))
    tr=[r for r in rows if r['kind']=='ref_trade']
    bb=[r for r in rows if r['kind']=='ref_bbo']
    print("  ref_trade %d  ref_bbo %d" % (len(tr),len(bb)))
    gaps=[(rows[i+1]['ev_ns']-rows[i]['ev_ns'])/1e9 for i in range(len(rows)-1)]
    print("  median gap %.3fs  p90 %.3fs  max %.1fs" %
          (st.median(gaps), sorted(gaps)[int(.9*len(gaps))], max(gaps)))
    print("  rows per minute: %.1f   (git snapshot had 3.72)" % (len(rows)/((t1-t0)/60)))
    sk=[r['skew_ns']/1e6 for r in rows]
    print("  venue->receipt skew: median %.2f ms  p90 %.2f ms  max %.1f ms" %
          (st.median(sk), sorted(sk)[int(.9*len(sk))], max(sk)))
    json.dump(rows, open('analysis/profit_search/tick/spot.json','w'))
    print("  saved analysis/profit_search/tick/spot.json")
