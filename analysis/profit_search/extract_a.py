"""Candidate A dataset: Gemini binary books aligned to the SETTLING reference rate.

The market settles on GRR-KAIKO_RFR_BTCUSD_60S and `spot_rfr` IS that series, so
the signal and the settlement share a source: no cross-venue basis to explain
away a result. Every row carries only information available at its own timestamp.
"""
import json, glob, collections, bisect

def load():
    rfr = collections.defaultdict(list)          # sym -> [(t, value)]
    books = collections.defaultdict(list)        # market -> [(t, b,B,a,A)]
    meta = {}                                    # market -> strike/expiry
    settle = {}                                  # market -> 'yes'/'no'
    trades = collections.defaultdict(list)       # market -> [(t, px, qty, buyer_is_maker)]
    for fn in sorted(glob.glob('data/ref_lag/*.jsonl')):
        for line in open(fn, errors='replace'):
            try: d = json.loads(line)
            except Exception: continue
            k = d.get('kind')
            if k == 'spot_rfr':
                try: rfr[d['sym']].append((d['t_ns']/1e9, float(d['rfr'])))
                except Exception: pass
            elif k == 'pbook':
                b, a = d.get('b'), d.get('a')
                if not b or not a: continue
                try:
                    books[d['market']].append((d['t_ns']/1e9, float(b), float(d.get('B') or 0),
                                               float(a), float(d.get('A') or 0)))
                except Exception: pass
            elif k == 'window':
                try:
                    meta[d['market']] = dict(strike=float(d['strike']),
                                             expiry=d['expiry'], source=d.get('source'))
                except Exception: pass
            elif k == 'settlement':
                rs = d.get('resolution_side')
                if rs in ('yes','no'): settle[d['market']] = 1 if rs == 'yes' else 0
            elif k == 'ptrade':
                try: trades[d['market']].append((d['t_ns']/1e9, float(d['price']),
                                                 float(d['qty']), bool(d.get('buyer_is_maker'))))
                except Exception: pass
    for v in rfr.values(): v.sort()
    for v in books.values(): v.sort()
    for v in trades.values(): v.sort()
    return rfr, books, meta, settle, trades

SYM = {'BTC':'BTCUSD','ETH':'ETHUSD','SOL':'SOLUSD','XRP':'XRPUSD'}

def sym_of(market):
    for a,s in SYM.items():
        if a in market: return s
    return None

def at_or_before(series, t):
    i = bisect.bisect_right(series, (t, float('inf'))) - 1
    return series[i] if i >= 0 else None

if __name__ == "__main__":
    import datetime as dt
    rfr, books, meta, settle, trades = load()
    print("rfr series:", {k: len(v) for k,v in rfr.items()})
    print("markets with books: %d   with window meta: %d   with settlement: %d"
          % (len(books), len(meta), len(settle)))
    usable = [m for m in books
              if m in meta and m in settle and sym_of(m) and len(books[m]) >= 10]
    print("USABLE markets (book + strike + settlement + spot): %d" % len(usable))
    fam = collections.Counter()
    for m in usable:
        fam[m.split('-')[1][:9].rstrip('0123456789')] += 1
    print("by family:", dict(fam))
    rows = 0
    out = []
    for m in usable:
        exp = dt.datetime.fromisoformat(meta[m]['expiry'].replace('Z','+00:00')).timestamp()
        s = rfr[sym_of(m)]
        ser = []
        for (t, b, B, a, A) in books[m]:
            if t >= exp: continue
            r = at_or_before(s, t)
            if r is None or t - r[0] > 120: continue      # stale reference: drop
            ser.append(dict(t=t, bid=b, bidq=B, ask=a, askq=A,
                            rfr=r[1], rfr_age=t-r[0], tte=exp-t))
        if len(ser) < 10: continue
        rows += len(ser)
        out.append(dict(market=m, strike=meta[m]['strike'], expiry=exp,
                        yes=settle[m], sym=sym_of(m), rows=ser,
                        ntrades=len(trades.get(m, []))))
    json.dump(out, open('analysis/profit_search/a_windows.json','w'))
    print("markets kept: %d   aligned book rows: %d" % (len(out), rows))
    print("settlement balance:", collections.Counter(w['yes'] for w in out))
