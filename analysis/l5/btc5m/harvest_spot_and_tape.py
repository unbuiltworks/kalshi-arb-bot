import gzip, json, sys, time, re, urllib.request, urllib.parse, datetime as dt, concurrent.futures as cf, os
BR = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
ACT = "/home/user/kalshi-arb-bot/analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
def get(u, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "research-client"}), timeout=40) as r:
                return json.loads(r.read())
        except Exception as e:
            time.sleep(1.5 * (i + 1))
    return None
def gemini():
    t = int(dt.datetime(2026,11,16,23,55,tzinfo=dt.timezone.utc).timestamp()*1000)
    end = int(dt.datetime(2026,11,20,14,10,tzinfo=dt.timezone.utc).timestamp()*1000)
    with gzip.open("gemini_btcusd_trades.jsonl.gz","wt") as f:
        n=0
        while t < end:
            d = get("https://api.gemini.com/v1/trades/btcusd?timestamp=%d&limit_trades=500" % t)
            if d is None: print("gemini fail at", t, flush=True); t += 60000; continue
            if not d: t += 600000; continue
            d.sort(key=lambda r:(r["timestampms"], r["tid"]))
            for r in d:
                f.write(json.dumps([r["timestampms"], r["price"], r["amount"], r["type"]])+"\n")
            n += len(d)
            nt = d[-1]["timestampms"] + 1
            t = nt if nt > t else t + 1000
            time.sleep(0.35)
    print("gemini done", n, flush=True)
def one(cs):
    c, slug = cs
    out=[]; cur=None
    for _ in range(40):
        u = "https://data-api.polymarket.com/v2/trades?condition=%s&limit=1000" % c
        if cur: u += "&cursor=" + urllib.parse.quote(cur)
        d = get(u)
        if d is None or "data" not in d: return slug, None
        for r in d["data"]:
            out.append([r["timestamp"], r["outcome"], r["side"], r["price"], r["size"], 1 if r["proxy_wallet"].lower()==BR else 0, r["transaction_hash"][:14]])
        p = d.get("pagination") or {}
        if not p.get("has_more"): break
        cur = p.get("next_cursor")
    return slug, out
def poly():
    conds = {}
    for l in gzip.open(ACT, "rt"):
        r = json.loads(l)
        if re.match(r"btc-updown-5m-\d+$", r.get("slug") or ""): conds[r["condition_id"]] = r["slug"]
    print("markets", len(conds), flush=True)
    bad=0
    with gzip.open("poly_trades.jsonl.gz","wt") as f, cf.ThreadPoolExecutor(4) as ex:
        for i,(slug,out) in enumerate(ex.map(one, sorted(conds.items(), key=lambda kv: kv[1]))):
            if out is None: bad+=1; print("fail", slug, flush=True); continue
            f.write(json.dumps({"slug": slug, "trades": out})+"\n")
            if i % 100 == 0: print("poly", i, len(out), flush=True)
    print("poly done; failed", bad, flush=True)
{"gemini": gemini, "poly": poly}[sys.argv[1]]()
