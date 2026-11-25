"""Read-only: does a 10-contract quote score on current live books in the
approved competitions? Public endpoints only. THEORETICAL."""
import json, sys, time, urllib.request
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
def get(p):
    r = urllib.request.Request(G + p, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f: return json.load(f)
OK = ("utr", "atp", "wta", "setkameua", "setkamemd", "atpdb", "wtadb")
now = time.time(); evs = []
for off in range(0, 3000, 200):
    d = get("/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off)
    e = d.get("events") or []; evs += e
    if len(e) < 200: break
slugs = sorted({m["slug"] for e in evs if e.get("live") for m in (e.get("markets") or [])
                if str(m.get("slug","")).startswith("aec-") and m["slug"].split("-")[1] in OK})
found = {}
for i in range(0, len(slugs), 60):
    f, _ = R.fetch_programs(get, slugs[i:i+60]); found.update(f)
pm = R.build_program_map(found); out = []
for s in slugs:
    prog = pm.get(s)
    per = R.paying_period(prog, now, event_live=True) if prog else None
    md = (get("/v1/markets/%s/book" % s) or {}).get("marketData") or {}
    time.sleep(0.25)
    b, a = R.book_sides(md)
    row = {"slug": s, "programme": bool(prog), "paying": bool(per)}
    if b and a:
        row.update(bid=b[0][0], ask=a[0][0], bq=round(b[0][1]), aq=round(a[0][1]))
    if per and b and a:
        row.update(pool_h=round(per["pool_rate_usd_s"]*3600, 2), target=per["targetSize"], ms=per["maxSpread"], df=per["df"])
        for tag, dy in (("touch", 0.0), ("behind", 0.01)):
            e = R.quote_estimate(md, per, b[0][0]-dy, 1-a[0][0]-dy, 10.0, 0.01)
            row[tag] = {"usd_h": round(e["est_usd_per_hour"], 3), "share": round(e["share"], 5),
                        "bid_inside": e["bid_inside"], "ask_inside": e["ask_inside"],
                        "max_spread_ok": e["max_spread_ok"]}
    out.append(row)
json.dump(out, open(sys.argv[1], "w"), indent=1)
for r in out:
    if "touch" in r:
        print(r["slug"][4:36].ljust(32), "bid", r["bid"], "ask", r["ask"], "touch", r["bq"], r["aq"], "| tgt", r["target"], "ms", r["ms"], "pool/h", r["pool_h"],
              "| AT TOUCH $%.3f/h inside %s/%s ms_ok %s" % (r["touch"]["usd_h"], r["touch"]["bid_inside"], r["touch"]["ask_inside"], r["touch"]["max_spread_ok"]),
              "| 1 BEHIND $%.3f/h inside %s/%s" % (r["behind"]["usd_h"], r["behind"]["bid_inside"], r["behind"]["ask_inside"]))
    else:
        print(r["slug"][4:36].ljust(32), "programme" if r["programme"] else "no programme", "paying" if r["paying"] else "not paying")
