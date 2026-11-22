"""Step 3: 1-minute international price history (YES token) for every matched
market over its PMUS quoting window (first order - 15 min .. last order + 2 min).
Writes price_history.json {pmus_slug: [[t, p], ...]}."""
import json, os, sys
from http_util import get, HERE
C = "https://clob.polymarket.com"
PNL = "/home/user/kalshi-arb-bot/analysis/l5/rewards/sports_market_pnl_20261114_20261118.json"
pnl = json.load(open(PNL))
mm = json.load(open(os.path.join(HERE, "market_map.json")))
out = {}
CH = 6 * 3600
for slug, r in mm.items():
    if not r["match"]:
        continue
    t0, t1 = int(pnl[slug]["t0"]) - 900, int(pnl[slug]["t1"]) + 120
    pts = {}
    a = t0
    while a < t1:
        b = min(a + CH, t1)
        d = get(f"{C}/prices-history?market={r['match']['yes_token']}&startTs={a}&endTs={b}&fidelity=1")
        for x in (d or {}).get("history", []):
            pts[int(x["t"])] = float(x["p"])
        a = b
    out[slug] = sorted(pts.items())
json.dump(out, open(os.path.join(HERE, "price_history.json"), "w"))
n0 = sum(1 for v in out.values() if not v)
print("markets fetched", len(out), "with no points", n0)
