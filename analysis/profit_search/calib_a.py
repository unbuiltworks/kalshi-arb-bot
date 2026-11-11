"""Does a no-lookahead diffusion model beat the market's own price?

If the market price is better calibrated than the model, candidate A has no
directional edge and dies here. Trailing volatility only: sigma at time t is
estimated from reference-rate returns strictly BEFORE t.
"""
import json, math, bisect, collections, statistics as st

W = json.load(open('analysis/profit_search/a_windows.json'))

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

# per-symbol trailing vol: sigma per sqrt(second), from the rfr path we observe
def build_vol(windows, lookback=1800.0):
    paths = collections.defaultdict(list)
    for w in windows:
        for r in w['rows']:
            paths[w['sym']].append((r['t'], r['rfr']))
    for k in paths:
        seen = {}
        for t, v in paths[k]: seen[round(t, 1)] = v
        paths[k] = sorted(seen.items())
    return paths

PATH = build_vol(W)

def sigma_at(sym, t, lookback=1800.0):
    p = PATH[sym]
    hi = bisect.bisect_left(p, (t, -1e18))
    lo = bisect.bisect_left(p, (t - lookback, -1e18))
    seg = p[lo:hi]
    if len(seg) < 12: return None
    rets, dts = [], []
    for i in range(1, len(seg)):
        dt = seg[i][0] - seg[i-1][0]
        if dt <= 0 or seg[i-1][1] <= 0: continue
        rets.append(math.log(seg[i][1]/seg[i-1][1])); dts.append(dt)
    if len(rets) < 10: return None
    tot = sum(dts)
    if tot <= 0: return None
    var = sum(r*r for r in rets) / tot            # per-second variance
    return math.sqrt(var) if var > 0 else None

rows = []
for w in W:
    K, exp, yes, sym = w['strike'], w['expiry'], w['yes'], w['sym']
    for r in w['rows']:
        s = sigma_at(sym, r['t'])
        if s is None or r['tte'] <= 0: continue
        S = r['rfr']
        if S <= 0: continue
        denom = s * math.sqrt(r['tte'])
        if denom <= 0: continue
        p_model = norm_cdf((math.log(S/K)) / denom)
        mid = (r['bid'] + r['ask']) / 2.0
        rows.append(dict(market=w['market'], fam=w['market'].split('-')[1][:6],
                         t=r['t'], tte=r['tte'], yes=yes, p_model=p_model,
                         mid=mid, bid=r['bid'], ask=r['ask'],
                         bidq=r['bidq'], askq=r['askq'],
                         moneyness=(S-K)/K, sigma=s))
print("scored rows: %d across %d markets" % (len(rows), len(set(r['market'] for r in rows))))

def brier(ps, ys): return sum((p-y)**2 for p,y in zip(ps,ys))/len(ps)
def logloss(ps, ys):
    e=1e-6
    return -sum(y*math.log(max(p,e))+(1-y)*math.log(max(1-p,e)) for p,y in zip(ps,ys))/len(ps)

ys=[r['yes'] for r in rows]
print("\nCALIBRATION against realized settlement (lower is better)")
print("  %-18s %-10s %-10s" % ("predictor","Brier","LogLoss"))
for lab,key in (("market mid","mid"),("diffusion model","p_model")):
    ps=[r[key] for r in rows]
    print("  %-18s %-10.5f %-10.5f" % (lab, brier(ps,ys), logloss(ps,ys)))
ps=[0.5]*len(rows); print("  %-18s %-10.5f %-10.5f" % ("constant 0.5", brier(ps,ys), logloss(ps,ys)))
base=st.mean(ys); ps=[base]*len(rows)
print("  %-18s %-10.5f %-10.5f  (base rate %.3f)" % ("base rate", brier(ps,ys), logloss(ps,ys), base))

print("\nby family:")
print("  %-8s %7s  %-9s %-9s  %s" % ("family","n","mid Brier","model Brier","winner"))
by=collections.defaultdict(list)
for r in rows: by[r['fam']].append(r)
for f in sorted(by):
    g=by[f]; y=[r['yes'] for r in g]
    bm=brier([r['mid'] for r in g],y); bo=brier([r['p_model'] for r in g],y)
    print("  %-8s %7d  %-9.5f %-9.5f  %s" % (f,len(g),bm,bo,"MARKET" if bm<bo else "model"))

print("\nby time to expiry:")
print("  %-12s %7s  %-9s %-9s  %s" % ("tte","n","mid Brier","model Brier","winner"))
for lo,hi,lab in ((0,60,"<60s"),(60,180,"1-3m"),(180,420,"3-7m"),(420,900,"7-15m"),(900,1e9,">15m")):
    g=[r for r in rows if lo<=r['tte']<hi]
    if len(g)<50: continue
    y=[r['yes'] for r in g]
    bm=brier([r['mid'] for r in g],y); bo=brier([r['p_model'] for r in g],y)
    print("  %-12s %7d  %-9.5f %-9.5f  %s" % (lab,len(g),bm,bo,"MARKET" if bm<bo else "model"))
json.dump(rows, open('analysis/profit_search/a_scored.json','w'))
