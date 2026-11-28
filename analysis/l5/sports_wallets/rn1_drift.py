"""Does RN1 buy before the price moves (information / speed) or after it has dropped (dip buying)?
For the tokens holding most of his dollars: public 1-minute price history around each buy.
  before = price N min BEFORE the buy minus price paid   (positive: he bought after a fall)
  after  = price N min AFTER  the buy minus price paid   (positive: it went his way)
Dollar-weighted by shares. Buys within 20 min of the last price point (settlement run) are excluded
from 'after'. READ-ONLY public data. usage: rn1_drift.py <activity.jsonl> <cache.json> <n_tokens>"""
import gzip, bisect, collections, json, os, sys, time, urllib.request
act, cache_p, ntok = sys.argv[1], sys.argv[2], int(sys.argv[3])
rows = [json.loads(l) for l in (gzip.open(act,'rt') if act.endswith('.gz') else open(act)) if '"TRADE"' in l]
usd = collections.Counter()
for r in rows: usd[r["token_id"]] += r["usdc_size"]
toks = [t for t, _ in usd.most_common(ntok)]
print("tokens %d cover %.0f%% of usdc" % (len(toks), 100 * sum(usd[t] for t in toks) / sum(usd.values())))
cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
t0, t1 = min(r["timestamp"] for r in rows) - 7200, max(r["timestamp"] for r in rows) + 7200
for i, t in enumerate(toks):
    if t in cache: continue
    u = "https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1" % (t, t0, t1)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "research-client"}), timeout=40) as f:
            cache[t] = [(h["t"], h["p"]) for h in json.load(f).get("history", [])]
    except Exception as e:
        cache[t] = []
    time.sleep(0.25)
    if i % 50 == 0: json.dump(cache, open(cache_p, "w"))
json.dump(cache, open(cache_p, "w"))
cache = {k: [tuple(x) for x in v] for k, v in cache.items()}
def at(h, ts, maxgap=180):
    k = bisect.bisect_right(h, (ts, 9)) - 1
    if k < 0 or ts - h[k][0] > maxgap: return None
    return h[k][1]
res = collections.defaultdict(lambda: [0.0, 0.0, 0])
slip = [0.0, 0.0]
for r in rows:
    h = cache.get(r["token_id"]) or []
    if len(h) < 30: continue
    ts, px, sh = r["timestamp"], r["price"], r["size"]
    band = "px<0.2" if px < .2 else "0.2-0.8" if px < .8 else "0.8-0.95" if px < .95 else ">=0.95"
    p0 = at(h, ts - 30)
    if p0 is not None: slip[0] += sh * (px - p0); slip[1] += sh
    for N in (2, 5, 15, 30):
        b = at(h, ts - N * 60)
        if b is not None:
            for key in (("before", N, "all"), ("before", N, band)):
                x = res[key]; x[0] += sh * (b - px); x[1] += sh; x[2] += 1
        if h[-1][0] - ts > 1200 + N * 60:
            a = at(h, ts + N * 60)
            if a is not None:
                for key in (("after", N, "all"), ("after", N, band)):
                    x = res[key]; x[0] += sh * (a - px); x[1] += sh; x[2] += 1
print("price paid minus last public price ~30s earlier: %+.4f a share" % (slip[0] / slip[1]))
for band in ("all", "px<0.2", "0.2-0.8", "0.8-0.95", ">=0.95"):
    print(band)
    for kind in ("before", "after"):
        print("  %-6s " % kind + "  ".join("%2dmin %+.4f (n %d)" % (N, res[(kind, N, band)][0] / max(res[(kind, N, band)][1], 1e-9), res[(kind, N, band)][2]) for N in (2, 5, 15, 30)))
