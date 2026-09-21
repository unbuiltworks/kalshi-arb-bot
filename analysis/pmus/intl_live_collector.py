"""Polymarket INTERNATIONAL live-sports habitat collector. READ-ONLY.

Public GETs only: gamma-api.polymarket.com for the live slate and
clob.polymarket.com for books. No credentials, no order path, no venue
writes. THIS VENUE IS NOT EXECUTABLE FOR US -- it is L5's own venue and is
measured here as the SOURCE HABITAT reference, never as a trading target.
No attempt is made to evade any geographic or platform restriction.

Rows use the SAME schema as the PMUS collector so the frozen analyzer runs
unchanged over both venues, with a `venue` field to keep them apart.

Liveness is the venue's OWN scoreboard flag (event.live true, ended false) --
the same authority the live UI uses -- not an inference from startDate, which
on this venue is market-creation time and marks season-long futures as
"started".
"""
import sys, os, json, time, threading, queue, ssl, urllib.request, collections
import datetime as dt

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
OUT=SCR+"/intl_live.jsonl"
GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
CAD=8.0
REDISCOVER_S=120
PER_FAMILY=2
LIVE_PER_EVENT=4
TOTAL_CAP=150
THREADS=12
END=dt.datetime(2026,9,21,6,0,tzinfo=dt.timezone.utc)

_CA = "/root/.ccr/ca-bundle.crt"
# The system trust store, not None: an explicit context is what the guard in
# tests/test_chain_ca_fallback.py checks for.
_ctx = (ssl.create_default_context(cafile=_CA) if os.path.exists(_CA)
        else ssl.create_default_context())

def get(url,timeout=20.0):
    req=urllib.request.Request(url,headers={"User-Agent":"research-readonly/1.0",
                                            "Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout,context=_ctx) as r:
        return json.loads(r.read().decode())

def rank(k):
    """Same explicit family priority the PMUS collector uses."""
    k=str(k or "")
    for i,pat in enumerate(("moneyline","full_game_winner","full_time_winner",
            "match_winner","spread","total","winner")):
        if pat in k: return (i,k)
    return (99,k)

def sport_of(e):
    s=e.get("sport")
    if isinstance(s,dict): return s.get("sport") or s.get("name") or "?"
    return s or (e.get("seriesSlug") or "?")

def discover():
    """Every live event the venue's own scoreboard reports, paginated."""
    evs=[]; off=0
    while off<600:
        try: p=get("%s/events?live=true&closed=false&limit=100&offset=%d"%(GAMMA,off))
        except Exception: break
        if not p: break
        evs+=p; off+=100
        if len(p)<100: break
    seen={}; sel=[]
    for e in evs:
        if e.get("id") in seen: continue
        seen[e.get("id")]=1
        if e.get("ended"): continue
        fam=collections.defaultdict(list)
        for m in (e.get("markets") or []):
            if m.get("closed") or not m.get("acceptingOrders"): continue
            try:
                b=float(m.get("bestBid") or 0); a=float(m.get("bestAsk") or 0)
            except (TypeError,ValueError): continue
            if not (0<b<a<1): continue
            try: toks=json.loads(m.get("clobTokenIds") or "[]")
            except Exception: continue
            if not toks: continue
            fam[m.get("sportsMarketType") or "?"].append((abs((b+a)/2-0.5),m,toks[0]))
        pick=[]
        for k in sorted(fam,key=rank):
            for _,m,tok in sorted(fam[k],key=lambda r:r[0])[:PER_FAMILY]:
                pick.append((m,tok))
        for m,tok in pick[:LIVE_PER_EVENT]:
            sel.append({"event":e.get("slug"),"league":sport_of(e),
                "slug":m.get("slug"),"token":tok,
                "type":m.get("sportsMarketType"),
                "sports_type":m.get("sportsMarketType"),
                "line":m.get("groupItemThreshold"),
                "kickoff_utc":m.get("gameStartTime"),
                "period":e.get("period"),"score":e.get("score"),
                "tick":m.get("orderPriceMinTickSize")})
    return sel[:TOTAL_CAP]

def top(book):
    bids=book.get("bids") or []; asks=book.get("asks") or []
    def px(L,hi):
        if not L: return None,None
        best=None
        for r in L:
            try: p=float(r["price"]); q=float(r["size"])
            except (TypeError,ValueError,KeyError): continue
            if best is None or (p>best[0] if hi else p<best[0]): best=(p,q)
        return best if best else (None,None)
    b,bq=px(bids,True); a,aq=px(asks,False)
    return b,a,bq,aq

out=open(OUT,"a",buffering=1)
lock=threading.Lock()
sel=discover(); last_disc=time.time()
print("INTL live: %d markets across %d events"%(len(sel),len({r["event"] for r in sel})),flush=True)
print(collections.Counter(r["league"] for r in sel).most_common(),flush=True)
prev=[None]; sweep=0
while dt.datetime.now(dt.timezone.utc)<END:
    sweep+=1; t0=time.time()
    if time.time()-last_disc>REDISCOVER_S:
        try:
            s2=discover()
            if s2: sel=s2
            last_disc=time.time()
            print("rediscover: %d markets  %d events  utc=%s"
                  %(len(sel),len({r["event"] for r in sel}),
                    dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
        except Exception as e:
            print("rediscover failed: %s"%type(e).__name__,flush=True)
    qq=queue.Queue()
    for r in sel: qq.put(r)
    def w():
        while True:
            try: r=qq.get_nowait()
            except queue.Empty: return
            try: bk=get("%s/book?token_id=%s"%(CLOB,r["token"]))
            except Exception: continue
            b,a,bq,aq=top(bk)
            with lock:
                out.write(json.dumps({"venue":"POLYMARKET_INTERNATIONAL",
                  "t":t0,"cad":CAD,"sweep_s":prev[0],
                  "slug":r["slug"],"league":r["league"],"event":r["event"],
                  "type":r["type"],"sports_type":r["sports_type"],"line":r["line"],
                  "kickoff_utc":r["kickoff_utc"],"live":True,"period":r["period"],
                  "event_state":{"period":r["period"],"score":r["score"],"live":True},
                  "bid":b,"ask":a,"bid_qty":bq,"ask_qty":aq,
                  "tick":r.get("tick")})+"\n")
    ths=[threading.Thread(target=w) for _ in range(THREADS)]
    [t.start() for t in ths]; [t.join() for t in ths]
    prev[0]=round(time.time()-t0,2)
    if sweep%50==0:
        print("sweep %d  markets=%d  sweep_s=%s  utc=%s"
              %(sweep,len(sel),prev[0],
                dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
    d=CAD-(time.time()-t0)
    if d>0: time.sleep(d)
out.close(); print("INTL DONE sweeps=%d"%sweep,flush=True)
