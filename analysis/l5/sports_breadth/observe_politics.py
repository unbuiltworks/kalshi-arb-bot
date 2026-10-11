import sys, json, asyncio, httpx, time
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src import l5_market_universe as U
from src import l5_sports_observer as O
SEC=float(sys.argv[1]) if len(sys.argv)>1 else 600.0
OUT="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad/observed_pol.json"
H={"User-Agent":"arb-laneD-readonly/1.0"}
c=httpx.Client(timeout=25.0, headers=H)
recs=[]
for cat in ("Politics","Economics"):
    for off in (0,500):
        r=c.get(U.GEMINI_REST+U.GEMINI_EVENTS, params=[("status","active"),("category",cat),
                                                       ("limit","500"),("offset",str(off))])
        d=r.json().get("data",[])
        recs += [x for x in U.gemini_markets(d) if x["tradeable"]]
        if len(d)<500: break
seen=set(); uniq=[]
for x in recs:
    if x["market_id"] in seen: continue
    seen.add(x["market_id"]); uniq.append(x)
uniq.sort(key=lambda r:-float(r.get("volume_24h") or 0))
sel=uniq[:30]   # BOUNDED: 102 symbols on one connection is what starved the last run
print(f"eligible {len(uniq)} -> observing {len(sel)} for {SEC}s", flush=True)
meta={r["market_id"]:r for r in sel}
ser=asyncio.run(O.observe_gemini([r["market_id"] for r in sel], meta, SEC))
out={"seconds":SEC,"cadence":{"gemini":O.GEMINI_RECORD_S},"series":{}}
for k,s in ser.items():
    if not s.obs and not s.one_sided: continue
    sm=s.summary(O.GEMINI_RECORD_S); sm["sport"]=sm.get("category")
    out["series"][f"GEMINI::{k}"]={"summary":sm,"obs":s.obs,"prints":s.prints,"pass":"GEMINI"}
json.dump(out, open(OUT,"w"), default=str)
print("WROTE series",len(out["series"]),"obs",sum(len(v["obs"]) for v in out["series"].values()),
      "prints",sum(len(v["prints"]) for v in out["series"].values()), flush=True)
