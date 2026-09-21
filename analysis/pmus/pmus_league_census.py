"""PMUS full-slate census on the venue's own event.live flag. PUBLIC READ-ONLY."""
import json,os,ssl,time,threading,queue,urllib.request
_CA = "/root/.ccr/ca-bundle.crt"
CTX = (ssl.create_default_context(cafile=_CA) if os.path.exists(_CA)
       else ssl.create_default_context())
def g(u,tries=3):
    for i in range(tries):
        try:
            r=urllib.request.Request(u,headers={"User-Agent":"l5-research/1.0","Accept":"application/json"})
            with urllib.request.urlopen(r,timeout=30,context=CTX) as h: return json.loads(h.read())
        except Exception:
            if i==tries-1: return None
            time.sleep(1.5*(i+1))
    return None
lg=json.load(open("pmus_leagues_sports.json")); listed=set(json.load(open("pmus_leagues_now.json")))
out={}; q=queue.Queue(); lock=threading.Lock()
for s in lg: q.put(s)
def px(v):
    if isinstance(v,dict):
        v=v.get("price") or v.get("value") or v.get("px")
        if isinstance(v,dict): v=v.get("value")
    try: return float(v)
    except (TypeError,ValueError): return None
def worker():
    while True:
        try: slug=q.get_nowait()
        except queue.Empty: return
        d=g("https://gateway.polymarket.us/v2/leagues/%s/events?limit=500"%slug)
        ev=(d or {}).get("events") or []
        r=dict(sport=lg[slug]["sport"],name=lg[slug]["name"],listed=slug in listed,
               events=len(ev),live_events=0,markets=0,live_markets=0,
               active_markets=0,two_sided=0,live_two_sided=0,live_slugs=[])
        for e in ev:
            islive=bool(e.get("live"))
            if islive: r["live_events"]+=1
            for m in (e.get("markets") or []):
                r["markets"]+=1
                if islive: r["live_markets"]+=1
                act=bool(m.get("active")) and not bool(m.get("closed"))
                if act: r["active_markets"]+=1
                b,a=px(m.get("bestBidQuote")),px(m.get("bestAskQuote"))
                two=(b is not None and a is not None and b>0 and a>0 and a>b)
                if two:
                    r["two_sided"]+=1
                    if islive:
                        r["live_two_sided"]+=1
                        if len(r["live_slugs"])<60: r["live_slugs"].append(m.get("slug"))
        with lock: out[slug]=r
ths=[threading.Thread(target=worker,daemon=True) for _ in range(8)]
[t.start() for t in ths]; [t.join() for t in ths]
json.dump(out,open("census265b.json","w"),indent=0)
print("done %d leagues"%len(out))
