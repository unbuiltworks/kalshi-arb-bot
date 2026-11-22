"""Read-only: once a minute, the top five levels and the cumulative traded
volume of each named PMUS market (public gateway only). Used to measure
pre-game price movement and how much actually trades against resting quotes.
usage: pregame_sampler.py <out.jsonl> <until_epoch> <slug> ..."""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
G = "https://gateway.polymarket.us"
def get(path, t=12):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as f:
        return json.load(f)
def lv(xs, rev):
    out = sorted(((float(x["px"]["value"]), float(x["qty"])) for x in xs or []), reverse=rev)
    return out[:5]
def one(slug):
    row = {"t": round(time.time(), 1), "slug": slug}
    try:
        md = get("/v1/markets/%s/book" % slug)["marketData"]
        row.update(state=md.get("state"), bids=lv(md.get("bids"), True), asks=lv(md.get("offers"), False))
    except Exception as e:
        row["book_err"] = type(e).__name__
    try:
        md = get("/v1/markets/%s/bbo" % slug)["marketData"]
        row.update(traded=float(md.get("sharesTraded") or 0), oi=float(md.get("openInterest") or 0),
                   last=float(((md.get("lastTradePx") or {}).get("value")) or 0))
    except Exception as e:
        row["bbo_err"] = type(e).__name__
    return row
out, until, slugs = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
ex = ThreadPoolExecutor(4)
with open(out, "a") as f:
    while time.time() < until:
        t0 = time.time()
        for row in ex.map(one, slugs):
            f.write(json.dumps(row) + "\n")
        f.flush()
        time.sleep(max(1.0, 60.0 - (time.time() - t0)))
