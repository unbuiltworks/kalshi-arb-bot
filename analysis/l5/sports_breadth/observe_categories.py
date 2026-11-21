"""P2: observe Gemini Politics / Weather / Commodities + crypto5m baseline."""
import sys, json, time, asyncio, httpx
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_market_universe as U
from src import l5_sports_observer as O
SECONDS=float(sys.argv[1]) if len(sys.argv)>1 else 600.0
OUT="/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad/observed_cat.json"
H={"User-Agent":"arb-laneD-readonly/1.0"}
cli=httpx.Client(timeout=25.0, headers=H)
def gem(p,q):
    r=cli.get(U.GEMINI_REST+p, params=q); r.raise_for_status(); return r.json()
rows,diag=U.gemini_events(gem)
recs=[r for r in U.gemini_markets(rows) if r["tradeable"]]
print("contracts",len(recs),diag,flush=True)
now=time.time()
def near(r,h=100000):
    t=r.get("tte_s"); return t is not None and 0<t<h*3600
sel=[]
for cat,n in (("Politics",26),("Weather",22),("Commodities",20),("Economics",8)):
    c=[r for r in recs if r["category"]==cat and near(r)]
    c.sort(key=lambda r:-float(r.get("volume_24h") or 0))
    sel+=c[:n]
    print(f"  {cat}: {len(c)} eligible -> observing {len(c[:n])}",flush=True)
cry=[r for r in recs if r.get("window_minutes")==5]
sel+=cry
print(f"  crypto5m baseline: {len(cry)}",flush=True)
meta={r["market_id"]:r for r in sel}
ser=asyncio.run(O.observe_gemini([r["market_id"] for r in sel], meta, SECONDS))
out={"seconds":SECONDS,"cadence":{"gemini":O.GEMINI_RECORD_S},"series":{}}
for k,s in ser.items():
    if not s.obs and not s.one_sided: continue
    out["series"][f"GEMINI::{k}"]={"summary":s.summary(O.GEMINI_RECORD_S),
                                   "obs":s.obs,"prints":s.prints,"pass":"GEMINI"}
json.dump(out, open(OUT,"w"), default=str)
print("WROTE", OUT, "series", len(out["series"]),
      "obs", sum(len(v["obs"]) for v in out["series"].values()),
      "prints", sum(len(v["prints"]) for v in out["series"].values()), flush=True)
