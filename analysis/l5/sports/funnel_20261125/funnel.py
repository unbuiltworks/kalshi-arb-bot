"""Read-only: every in-play sports market now, each selection/quote filter
of the production sports bot applied independently and in order, plus the
reward-adjusted net of our own quote (same model as PMUS_RN1_NET_RANK) at
several loss assumptions. Public endpoints only. THEORETICAL."""
import json, sys, time, urllib.request
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
ESPORTS = {"apex","cod","cs","cs2","csgo","dota","dota2","esports","ff","hok","kog","lol","mlbb","ow","pubg","r6","rl","sc2","val","valorant","ebfpl","ebfsa","ebbec","ebb"}
CRICKET = {"cr","sheffield"}
ALLOW = {"setkameua","setkamemd","utr","atp","wta","atpdb","wtadb"}
BAND = (0.30, 0.70); PX = (0.20, 0.92); SPREAD_TICKS = 6; MIN_TOUCH = 3; CAP = 0.97
RATE = {0: 3.0, 1: 1.5}
LOSSES = {"campaign (5c/3.5c)": {0: .05, 1: .035}, "measured markout (6c/1.4c)": {0: .06, 1: .014}, "half (2.5c/1.75c)": {0: .025, 1: .0175}}
def get(p):
    r = urllib.request.Request(G + p, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f: return json.load(f)
evs = []
for off in range(0, 4000, 200):
    e = get("/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off).get("events") or []
    evs += e
    if len(e) < 200: break
live = [(m["slug"], e.get("title") or "") for e in evs if e.get("live") for m in (e.get("markets") or [])]
now = time.time()
f = {}
for i in range(0, len(live), 60):
    ff, _ = R.fetch_programs(get, [s for s, _ in live[i:i+60]]); f.update(ff)
pm = R.build_program_map(f)
rows = []
for s, title in live:
    comp = s.split("-")[1] if "-" in s else ""
    md = (get("/v1/markets/%s/book" % s) or {}).get("marketData") or {}
    time.sleep(0.15)
    b, a = R.book_sides(md)
    per = R.paying_period(pm.get(s), now, event_live=True) if pm.get(s) else None
    r = {"slug": s, "title": title[:50], "comp": comp}
    if b and a:
        bb, ba = b[0][0], a[0][0]
        r.update(bid=bb, ask=ba, bq=b[0][1], aq=a[0][1])
    rej = {}
    rej["cricket_esports"] = comp in ESPORTS or comp in CRICKET
    rej["competition"] = comp not in ALLOW
    rej["no_two_sided"] = not (b and a)
    rej["band_30_70"] = bool(b and a) and not (BAND[0] <= b[0][0] <= BAND[1])
    rej["spread_gt_6"] = bool(b and a) and round(a[0][0] - b[0][0], 4) > SPREAD_TICKS * 0.01 + 1e-9
    rej["no_paying_programme"] = per is None
    rej["touch_depth_lt_3"] = bool(b and a) and (b[0][1] < MIN_TOUCH or a[0][1] < MIN_TOUCH)
    r["rej"] = rej
    if per and b and a:
        nets = {}
        for lname, L in LOSSES.items():
            best = None
            for q in (1, 2, 5, 10):
                for d in (0, 1):
                    y, n = round(b[0][0] - d*.01, 2), round(1 - a[0][0] - d*.01, 2)
                    ov = round(y + n - CAP, 4)
                    if ov > 0:
                        if y >= n: y = round(y - ov, 2)
                        else: n = round(n - ov, 2)
                    sides = [(px) for px in (y, n) if PX[0] <= px <= PX[1]]
                    # sides outside the per-bid band are not quoted
                    yq = y if PX[0] <= y <= PX[1] else None
                    nq = n if PX[0] <= n <= PX[1] else None
                    if yq is None and nq is None: continue
                    e = R.quote_estimate(md, per, yq, nq, float(q), 0.01)
                    nside = (yq is not None) + (nq is not None)
                    cost = nside * q * RATE[d] * L[d]
                    v = (round(e["est_usd_per_hour"] - cost, 3), q, d, round(e["est_usd_per_hour"], 3), round(cost, 3), nside)
                    if best is None or v[0] > best[0]: best = v
            nets[lname] = best
        r["net"] = nets
        r["pool_h"] = round(per["pool_rate_usd_s"] * 3600, 2); r["target"] = per["targetSize"]
    rows.append(r)
json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "rows": rows}, open(sys.argv[1], "w"), indent=1)
