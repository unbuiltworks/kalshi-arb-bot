"""Extract Kalshi BTC15m full-depth book snapshots from persisted log dumps."""
import json, sys, datetime as dt, statistics as st, collections

def extract(paths):
    out=[]
    for p in paths:
        d=json.load(open(p))
        for e in d.get('deploy') or []:
            m=e.get('message') or ''
            i=m.find('PMUS_TAPE_ROW')
            if i<0: continue
            try: o=json.loads(m[i+len('PMUS_TAPE_ROW'):])
            except Exception: continue
            if o.get('kind')!='book': continue
            try:
                t=dt.datetime.fromisoformat(o['t']).timestamp()
                tt=o.get('transact_time')
                tv=dt.datetime.fromisoformat(tt.replace('Z','+00:00')).timestamp() if tt else None
                out.append(dict(t=t, venue_t=tv, slug=o['slug'], state=o.get('state'),
                                bids=[[float(a),float(b)] for a,b in (o.get('bids') or [])],
                                offers=[[float(a),float(b)] for a,b in (o.get('offers') or [])]))
            except Exception: continue
    out.sort(key=lambda r:r['t'])
    return out

if __name__=="__main__":
    rows=extract(sys.argv[1:])
    print("book snapshots: %d" % len(rows))
    if not rows: sys.exit()
    print("span %s -> %s (%.1f min)" %
          (dt.datetime.fromtimestamp(rows[0]['t'],dt.UTC).strftime('%H:%M:%S'),
           dt.datetime.fromtimestamp(rows[-1]['t'],dt.UTC).strftime('%H:%M:%S'),
           (rows[-1]['t']-rows[0]['t'])/60))
    g=[rows[i+1]['t']-rows[i]['t'] for i in range(len(rows)-1)]
    print("  median cadence %.2fs  p90 %.2fs" % (st.median(g),sorted(g)[int(.9*len(g))]))
    print("  depth levels: median bids %d  offers %d" %
          (st.median([len(r['bids']) for r in rows]), st.median([len(r['offers']) for r in rows])))
    lag=[(r['t']-r['venue_t'])*1000 for r in rows if r['venue_t']]
    if lag: print("  venue->receipt lag: median %.1f ms  p90 %.1f ms" %
                  (st.median(lag),sorted(lag)[int(.9*len(lag))]))
    c=collections.Counter(r['slug'] for r in rows)
    print("  windows covered:")
    for s,n in sorted(c.items()):
        g2=[r for r in rows if r['slug']==s]
        print("    %-42s %4d snaps  %s -> %s" %
              (s,n,dt.datetime.fromtimestamp(g2[0]['t'],dt.UTC).strftime('%H:%M:%S'),
               dt.datetime.fromtimestamp(g2[-1]['t'],dt.UTC).strftime('%H:%M:%S')))
    json.dump(rows, open('analysis/profit_search/tick/book.json','w'))
    print("  saved analysis/profit_search/tick/book.json")
