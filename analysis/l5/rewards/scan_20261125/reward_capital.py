"""Read-only: theoretical reward vs order size on live paying books.
Public endpoints only. THEORETICAL; per-market pool reading (upper bound)."""
import json, sys, time, urllib.request, datetime as dt, collections
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
def get(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f: return json.load(f)
now = time.time()
evs = []
for off in range(0, 3000, 200):
    d = get("/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off)
    e = d.get("events") or []; evs += e
    if len(e) < 200: break
rows = {}
for e in evs:
    if not e.get("live"): continue
    for m in e.get("markets") or []:
        s = str(m.get("slug") or "")
        lg = s.split("-")[1] if s.count("-") > 1 else ""
        if s.startswith("aec-") and "cr" not in lg[-2:] and lg not in ("sheffield","wbbl","smat","csafc","pro50","testcr"):
            rows[s] = lg
slugs = sorted(rows); found = {}
for i in range(0, len(slugs), 60):
    f, _ = R.fetch_programs(get, slugs[i:i+60]); found.update(f)
pm = R.build_program_map(found)
QS = (2, 5, 10, 25, 50, 100, 250)
out = []
for s in slugs:
    prog = pm.get(s)
    if not prog: continue
    per = R.paying_period(prog, now, event_live=True)
    if not per: continue
    try: md = (get("/v1/markets/%s/book" % s) or {}).get("marketData") or {}
    except Exception: continue
    time.sleep(0.25)
    b, a = R.book_sides(md)
    if not b or not a: continue
    bb, ba = b[0][0], a[0][0]
    if not (0.20 <= bb <= 0.80): continue
    r = {"slug": s, "lg": rows[s], "pool": per["pool"], "pool_h": per["pool_rate_usd_s"]*3600, "target": per["targetSize"], "ms": per["maxSpread"], "bid": bb, "ask": ba, "bq": b[0][1], "aq": a[0][1]}
    for q in QS:
        j = R.quote_estimate(md, per, bb, 1 - ba, float(q), 0.01)
        k = R.quote_estimate(md, per, bb - 0.01, 1 - ba - 0.01, float(q), 0.01)
        r["j%d" % q] = j["est_usd_per_hour"]; r["b%d" % q] = k["est_usd_per_hour"]
        r["cap%d" % q] = round(q * (bb + 1 - ba), 2)
    out.append(r)
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(len(slugs), "live aec markets;", len(out), "paying, two-sided, bid 0.20-0.80")
