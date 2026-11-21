"""TRADED-FLOW tap for both venues. READ-ONLY public GETs.

This is the instrument that separates BOOK REACH habitat from EXECUTABLE
RECYCLING habitat. The habitat collectors record books; this records what
actually TRADED.

INTERNATIONAL -- REAL PRINTS, NOT INFERENCE. data-api /trades returns the
global recent tape: limit=1000 spans ~48s across ~212 markets, so ONE call
every 20s captures essentially every print on the venue. Each row carries
price, size, side and wallet, so trade DIRECTION IS KNOWN and never inferred.
Prints are deduped by (transactionHash, timestamp, size, price, asset).

PMUS -- CAUSAL VOLUME DELTA, NO HINDSIGHT. /bbo carries cumulative
sharesTraded plus lastTradePx and the touch. Flow is the delta between
consecutive snapshots, which is exactly the inference the frozen decision
layer was built on. Nothing is read backwards from a later BBO.

Neither venue's numbers are blended with the other's; provenance is stamped
on every row.
"""
import sys, os, json, time, threading, ssl, urllib.request, collections, queue
import datetime as dt
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src import pmus_sports_collector as col

SCR="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
GAMMA="https://gamma-api.polymarket.com"
DATA="https://data-api.polymarket.com"
PMUS_CAD=8.0
INTL_CAD=20.0
REDISCOVER_S=180
PMUS_CAP=120
END=dt.datetime(2026,9,21,6,0,tzinfo=dt.timezone.utc)

_ctx=ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt") \
     if os.path.exists("/root/.ccr/ca-bundle.crt") else None
def get(u,t=25.0):
    r=urllib.request.Request(u,headers={"User-Agent":"research-readonly/1.0",
                                        "Accept":"application/json"})
    with urllib.request.urlopen(r,timeout=t,context=_ctx) as x:
        return json.loads(x.read().decode())
def q(v):
    v=v.get("value") if isinstance(v,dict) else v
    try: return float(v)
    except (TypeError,ValueError): return None
def iso(t): return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()

fp=open(SCR+"/flow_pmus.jsonl","a",buffering=1)
fi=open(SCR+"/flow_intl.jsonl","a",buffering=1)
lock=threading.Lock()

def pmus_live():
    out=[]
    try: lgs=[l["slug"] for l in col.http_get_json("/v2/leagues")["leagues"]]
    except Exception: return out
    for lg in lgs:
        try: ev=col.http_get_json("/v2/leagues/%s/events?limit=500"%lg)
        except Exception: continue
        for e in (ev.get("events") or []):
            st=e.get("eventState") or {}
            if not st.get("live") or st.get("ended"): continue
            for m in (e.get("markets") or []):
                if not m.get("slug"): continue
                b,a=q(m.get("bestBidQuote")),q(m.get("bestAskQuote"))
                if b is None or a is None or not (0<b<a<1): continue
                out.append({"slug":m["slug"],"league":lg,"event":e.get("slug"),
                            "sports_type":m.get("sportsMarketType")})
    return out[:PMUS_CAP]

def intl_map():
    """conditionId -> market identity, for the live sports slate."""
    mp={}; off=0
    while off<600:
        try: p=get("%s/events?live=true&closed=false&limit=100&offset=%d"%(GAMMA,off))
        except Exception: break
        if not p: break
        for e in p:
            if e.get("ended"): continue
            sp=e.get("sport"); sp=sp.get("sport") if isinstance(sp,dict) else sp
            for m in (e.get("markets") or []):
                if m.get("conditionId"):
                    mp[m["conditionId"]]={"slug":m.get("slug"),"league":sp or "?",
                        "event":e.get("slug"),
                        "sports_type":m.get("sportsMarketType")}
        off+=100
        if len(p)<100: break
    return mp

state={"pmus":pmus_live(),"intl":intl_map(),"t":time.time()}
print("flow tap: PMUS live markets=%d   INTL live conditions=%d"
      %(len(state["pmus"]),len(state["intl"])),flush=True)

def pmus_loop():
    sweep=0
    while dt.datetime.now(dt.timezone.utc)<END:
        t0=time.time(); sweep+=1
        qq=queue.Queue()
        for r in state["pmus"]: qq.put(r)
        def w():
            while True:
                try: r=qq.get_nowait()
                except queue.Empty: return
                try: b=col.http_get_json("/v1/markets/%s/bbo"%r["slug"])["marketData"]
                except Exception: continue
                with lock:
                    fp.write(json.dumps({"venue":"PMUS","t":iso(t0),
                      "slug":r["slug"],"league":r["league"],"event":r["event"],
                      "sports_type":r["sports_type"],
                      "bid":q(b.get("bestBid")),"ask":q(b.get("bestAsk")),
                      "shares_traded_cum":q(b.get("sharesTraded")),
                      "last_trade_px":q(b.get("lastTradePx")),
                      "open_interest":q(b.get("openInterest")),
                      "trade_provenance":"VOLUME_DELTA_CAUSAL_NO_HINDSIGHT"})+"\n")
        ths=[threading.Thread(target=w) for _ in range(10)]
        [t.start() for t in ths]; [t.join() for t in ths]
        if sweep%50==0:
            print("PMUS flow sweep %d markets=%d sweep_s=%.2f utc=%s"
                  %(sweep,len(state["pmus"]),time.time()-t0,
                    dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
        d=PMUS_CAD-(time.time()-t0)
        if d>0: time.sleep(d)

def intl_loop():
    seen=collections.deque(maxlen=60000); seenset=set(); sweep=0
    while dt.datetime.now(dt.timezone.utc)<END:
        t0=time.time(); sweep+=1
        try: tr=get("%s/trades?limit=1000"%DATA)
        except Exception: tr=[]
        n=0
        for x in (tr if isinstance(tr,list) else []):
            cid=x.get("conditionId")
            ident=state["intl"].get(cid)
            if ident is None: continue            # not a live sports market
            k=(x.get("transactionHash"),x.get("timestamp"),x.get("size"),
               x.get("price"),x.get("asset"))
            if k in seenset: continue
            seenset.add(k); seen.append(k)
            if len(seen)==seen.maxlen:
                old=seen.popleft(); seenset.discard(old)
            with lock:
                fi.write(json.dumps({"venue":"INTL","observed_t":iso(t0),
                  "t":x.get("timestamp"),"slug":ident["slug"],
                  "league":ident["league"],"event":ident["event"],
                  "sports_type":ident["sports_type"],
                  "condition":cid,"asset":x.get("asset"),
                  "price":x.get("price"),"size":x.get("size"),
                  "side":x.get("side"),"outcome":x.get("outcome"),
                  "outcome_index":x.get("outcomeIndex"),
                  "wallet":x.get("proxyWallet"),
                  "trade_provenance":"REAL_PRINT_DIRECTION_KNOWN"})+"\n")
            n+=1
        if sweep%20==0:
            print("INTL flow sweep %d new_prints=%d conditions=%d utc=%s"
                  %(sweep,n,len(state["intl"]),
                    dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
        d=INTL_CAD-(time.time()-t0)
        if d>0: time.sleep(d)

def disc_loop():
    while dt.datetime.now(dt.timezone.utc)<END:
        time.sleep(REDISCOVER_S)
        try:
            p=pmus_live(); m=intl_map()
            if p: state["pmus"]=p
            if m: state["intl"]=m
            print("rediscover: PMUS %d  INTL %d  utc=%s"
                  %(len(state["pmus"]),len(state["intl"]),
                    dt.datetime.now(dt.timezone.utc).strftime("%H:%M")),flush=True)
        except Exception as e:
            print("rediscover failed %s"%type(e).__name__,flush=True)

for fn in (pmus_loop,intl_loop,disc_loop):
    threading.Thread(target=fn,daemon=True).start()
while dt.datetime.now(dt.timezone.utc)<END: time.sleep(5)
print("FLOW TAP DONE",flush=True)
