"""Shadow FEED for the frozen L5 continuous-inventory policy, both venues.

READ-ONLY. Public GETs only. No credentials, no order path, no arming, no
venue writes. Nothing here is submitted, filled, settled or realized.

Emits rows in EXACTLY the shape src.pmus_sports_l5_decision expects
(t ISO string, bid, ask, shares_traded_cum, last_trade_px) so the FROZEN
decision layer runs unmodified over both venues.

SELECTION, FIXED BEFORE ANY SHADOW RESULT EXISTS: the match-winner /
moneyline family on currently-live events -- the binary that corresponds to
the L5 source product -- two-sided only, capped per venue. Chosen because
every top-ranked market in the habitat ranking sits in that family, not by
tuning against shadow output. Markets are re-selected as games turn over.

TRADE PROVENANCE DIFFERS BY VENUE AND IS LABELLED, NEVER BLENDED:
  PMUS  cumulative sharesTraded from /bbo -> volume-delta INFERENCE, the same
        upper-bound method the frozen layer was built on.
  INTL  REAL per-trade prints from data-api (price, size, side). Direction is
        KNOWN, not inferred. Cumulative volume is summed from those prints.
"""
import sys, os, json, time, threading, queue, ssl, urllib.request, collections
import datetime as dt

sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src import pmus_sports_collector as col

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
DATA="https://data-api.polymarket.com"
CAD=4.0
REDISCOVER_S=180
PER_VENUE=8
END=dt.datetime(2026,9,21,6,0,tzinfo=dt.timezone.utc)
WINNER=("moneyline","full_game_winner","full_time_winner","match_winner","winner")
# L5's operating region. The same [0.30,0.70] band the habitat collector has
# used since preregistration -- NOT a new gate fitted to shadow output. Sorting
# purely by narrowest spread selected near-zero-priced markets (an Eredivisie
# draw at bid 0.002), which the frozen model refuses as v2_off_distribution and
# which would have burned shadow slots on states the policy never acts in.
BAND_LO,BAND_HI=0.30,0.70

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

def iso(t): return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
def isw(k): return any(p in str(k or "") for p in WINNER)

def q(v):
    v=v.get("value") if isinstance(v,dict) else v
    try: return float(v)
    except (TypeError,ValueError): return None

def pick_pmus():
    out=[]
    try: lgs=[l["slug"] for l in col.http_get_json("/v2/leagues")["leagues"]]
    except Exception: return out
    for lg in lgs:
        try: ev=col.http_get_json("/v2/leagues/%s/events?limit=500"%lg)
        except Exception: continue
        for e in (ev.get("events") or []):
            stt=e.get("eventState") or {}
            if not stt.get("live") or stt.get("ended"): continue
            for m in (e.get("markets") or []):
                if not m.get("slug") or not isw(m.get("sportsMarketType")): continue
                b,a=q(m.get("bestBidQuote")),q(m.get("bestAskQuote"))
                if b is None or a is None or not (0<b<a<1): continue
                if not (BAND_LO<=(a+b)/2<=BAND_HI): continue
                out.append({"venue":"PMUS","slug":m["slug"],"league":lg,
                            "event":e.get("slug"),
                            "sports_type":m.get("sportsMarketType"),
                            "spread":a-b,"mid":(a+b)/2})
    out.sort(key=lambda r:(r["spread"],abs(r["mid"]-0.5)))
    return out[:PER_VENUE]

def pick_intl():
    evs=[]; off=0
    while off<600:
        try: p=get("%s/events?live=true&closed=false&limit=100&offset=%d"%(GAMMA,off))
        except Exception: break
        if not p: break
        evs+=p; off+=100
        if len(p)<100: break
    out=[]; seen=set()
    for e in evs:
        if e.get("id") in seen or e.get("ended"): continue
        seen.add(e.get("id"))
        sp=e.get("sport"); sp=sp.get("sport") if isinstance(sp,dict) else sp
        for m in (e.get("markets") or []):
            if m.get("closed") or not m.get("acceptingOrders"): continue
            if not isw(m.get("sportsMarketType")): continue
            try:
                b=float(m.get("bestBid") or 0); a=float(m.get("bestAsk") or 0)
            except (TypeError,ValueError): continue
            if not (0<b<a<1): continue
            try: toks=json.loads(m.get("clobTokenIds") or "[]")
            except Exception: continue
            if not toks: continue
            if not (BAND_LO<=(a+b)/2<=BAND_HI): continue
            out.append({"venue":"INTL","slug":m.get("slug"),"league":sp or "?",
                        "event":e.get("slug"),"token":toks[0],
                        "condition":m.get("conditionId"),
                        "sports_type":m.get("sportsMarketType"),
                        "spread":a-b,"mid":(a+b)/2})
    out.sort(key=lambda r:(r["spread"],abs(r["mid"]-0.5)))
    return out[:PER_VENUE]

fh={"PMUS":open(SCR+"/shadow_pmus.jsonl","a",buffering=1),
    "INTL":open(SCR+"/shadow_intl.jsonl","a",buffering=1)}
lock=threading.Lock()
cum=collections.defaultdict(float)      # INTL synthetic cumulative volume
lastpx={}
seen_tr=collections.defaultdict(set)

def poll_pmus(r,t0):
    try: b=col.http_get_json("/v1/markets/%s/bbo"%r["slug"])["marketData"]
    except Exception: return None
    return {"t":iso(t0),"venue":"PMUS","slug":r["slug"],"league":r["league"],
            "event":r["event"],"sports_type":r["sports_type"],
            "bid":q(b.get("bestBid")),"ask":q(b.get("bestAsk")),
            "shares_traded_cum":q(b.get("sharesTraded")),
            "last_trade_px":q(b.get("lastTradePx")),
            "open_interest":q(b.get("openInterest")),
            "trade_provenance":"VOLUME_DELTA_INFERRED"}

def poll_intl(r,t0):
    try: bk=get("%s/book?token_id=%s"%(CLOB,r["token"]))
    except Exception: return None
    def top(L,hi):
        best=None
        for x in L or []:
            try: p=float(x["price"]); s=float(x["size"])
            except (TypeError,ValueError,KeyError): continue
            if best is None or (p>best[0] if hi else p<best[0]): best=(p,s)
        return best or (None,None)
    bid,bq=top(bk.get("bids"),True); ask,aq=top(bk.get("asks"),False)
    try: tr=get("%s/trades?market=%s&limit=100"%(DATA,r["condition"]))
    except Exception: tr=[]
    key=r["slug"]; new=0.0
    for x in (tr if isinstance(tr,list) else []):
        tid=(x.get("transactionHash"),x.get("timestamp"),x.get("size"),x.get("price"))
        if tid in seen_tr[key]: continue
        seen_tr[key].add(tid)
        try:
            new+=float(x.get("size") or 0); lastpx[key]=float(x.get("price"))
        except (TypeError,ValueError): pass
    cum[key]+=new
    return {"t":iso(t0),"venue":"INTL","slug":key,"league":r["league"],
            "event":r["event"],"sports_type":r["sports_type"],
            "bid":bid,"ask":ask,"bid_qty":bq,"ask_qty":aq,
            "shares_traded_cum":round(cum[key],4),
            "last_trade_px":lastpx.get(key),
            "prints_this_cycle":len([1 for _ in range(0)]) or None,
            "trade_provenance":"REAL_PRINTS_DIRECTION_KNOWN"}

sel=pick_pmus()+pick_intl(); last=time.time()
print("shadow feed: PMUS %d  INTL %d"%(sum(1 for r in sel if r["venue"]=="PMUS"),
      sum(1 for r in sel if r["venue"]=="INTL")),flush=True)
for r in sel: print("   %-5s %-46s %s spread=%.4f"%(r["venue"],r["slug"][:46],r["league"],r["spread"]),flush=True)
sweep=0
while dt.datetime.now(dt.timezone.utc)<END:
    sweep+=1; t0=time.time()
    if time.time()-last>REDISCOVER_S:
        try:
            s2=pick_pmus()+pick_intl()
            if s2:
                sel=s2
                print("reselect: %d markets utc=%s"%(len(sel),
                      dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
        except Exception as e: print("reselect failed %s"%type(e).__name__,flush=True)
        last=time.time()
    qq=queue.Queue()
    for r in sel: qq.put(r)
    def w():
        while True:
            try: r=qq.get_nowait()
            except queue.Empty: return
            row=poll_pmus(r,t0) if r["venue"]=="PMUS" else poll_intl(r,t0)
            if row is None: continue
            with lock: fh[r["venue"]].write(json.dumps(row)+"\n")
    ths=[threading.Thread(target=w) for _ in range(8)]
    [t.start() for t in ths]; [t.join() for t in ths]
    if sweep%100==0:
        print("sweep %d  markets=%d  sweep_s=%.2f  utc=%s"
              %(sweep,len(sel),time.time()-t0,
                dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
    d=CAD-(time.time()-t0)
    if d>0: time.sleep(d)
print("SHADOW FEED DONE",flush=True)
