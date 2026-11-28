"""Read-only: expected NET per hour (THEORETICAL reward minus expected fill
cost) for our own quote on current live books, per market, at the best bid
and one tick behind, for clip sizes 1/2/5/10. Public endpoints only. The
same model as PMUS_RN1_NET_RANK. Estimates, not payments."""
import json, sys, time, urllib.request
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
RATE = {0: float(sys.argv[2]), 1: float(sys.argv[3])}    # contracts filled per resting contract-hour
LOSS = {0: float(sys.argv[4]), 1: float(sys.argv[5])}    # $ per filled contract
CAP = 0.97
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
    if not per: continue
    md = (get("/v1/markets/%s/book" % s) or {}).get("marketData") or {}
    time.sleep(0.25)
    b, a = R.book_sides(md)
    if not b or not a: continue
    bb, ba = b[0][0], a[0][0]
    best = None; rows = []
    for q in (1, 2, 5, 10):
        for d in (0, 1):
            y, n = round(bb - d*0.01, 2), round(1 - ba - d*0.01, 2)
            over = round(y + n - CAP, 4)
            if over > 0:
                if y >= n: y = round(y - over, 2)
                else: n = round(n - over, 2)
            if not (0 < y < 1 and 0 < n < 1): continue
            e = R.quote_estimate(md, per, y, n, float(q), 0.01)
            rew = float(e["est_usd_per_hour"]); cost = 2*q*RATE[d]*LOSS[d]
            r = {"q": q, "d": d, "y": y, "n": n, "rew": round(rew, 3), "cost": round(cost, 3),
                 "net": round(rew - cost, 3), "cap": round(q*(y+n), 2), "ms_ok": e["max_spread_ok"]}
            rows.append(r)
            if best is None or r["net"] > best["net"]: best = r
    out.append({"slug": s, "bid": bb, "ask": ba, "bq": round(b[0][1]), "aq": round(a[0][1]),
                "pool_h": round(per["pool_rate_usd_s"]*3600, 2), "target": per["targetSize"],
                "ms": per["maxSpread"], "best": best, "rows": rows})
out.sort(key=lambda r: -(r["best"] or {"net": -9})["net"])
json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "rate": RATE, "loss": LOSS,
           "markets": out}, open(sys.argv[1], "w"), indent=1)
print("paying in-scope live markets with two-sided books:", len(out))
for r in out[:25]:
    b = r["best"]; g = max((x for x in r["rows"] if x["q"] == 10), key=lambda x: x["rew"], default=None)
    print(r["slug"][4:40].ljust(36), "%.2f/%.2f" % (r["bid"], r["ask"]), "tgt", r["target"], "pool/h", r["pool_h"],
          "| best q%s %s rew %.3f cost %.3f NET %.3f cap %.2f" % (b["q"], "touch" if b["d"] == 0 else "behind", b["rew"], b["cost"], b["net"], b["cap"]),
          "| q10 best-reward %.3f net %.3f" % (g["rew"], g["net"]) if g else "")
