"""Read-only paired sampler: the same game on Polymarket US and Polymarket international, a few seconds apart.
Question it serves: when one venue's price moves, does the other lag long enough to trade against?
Public keyless GETs only. One JSON line per game per sample: both venues' best bid/ask for the FIRST outcome.
usage: cross_venue_sampler.py <out.jsonl> <until_epoch> <intl_slug> [<intl_slug> ...]   (US slug = 'aec-' + intl slug)"""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "research-client"}), timeout=10) as r:
        return json.load(r)
def setup(slug):
    m = get("https://gamma-api.polymarket.com/markets?slug=" + slug)[0]
    return {"slug": slug, "tok": json.loads(m["clobTokenIds"])[0], "outcome": json.loads(m["outcomes"])[0], "start": m.get("gameStartTime")}
def sample(g):
    row = {"slug": g["slug"]}
    try:
        t = time.time(); b = get("https://clob.polymarket.com/book?token_id=" + g["tok"])
        bids = [float(x["price"]) for x in b.get("bids", [])]; asks = [float(x["price"]) for x in b.get("asks", [])]
        row.update(ti=round(t, 2), ib=max(bids) if bids else None, ia=min(asks) if asks else None)
    except Exception as e: row["ie"] = type(e).__name__
    try:
        t = time.time(); d = get("https://gateway.polymarket.us/v1/markets/aec-%s/bbo" % g["slug"])["marketData"]
        row.update(tu=round(t, 2), ub=float(d["bestBid"]["value"]) if d.get("bestBid") else None,
                   ua=float(d["bestAsk"]["value"]) if d.get("bestAsk") else None, us=d.get("state"),
                   ubd=d.get("bidDepth"), uad=d.get("askDepth"), ush=d.get("sharesTraded"))
    except Exception as e: row["ue"] = type(e).__name__
    return row
if __name__ == "__main__":
    out, until = sys.argv[1], float(sys.argv[2]); games = []
    for s in sys.argv[3:]:
        try: games.append(setup(s))
        except Exception as e: print("skip", s, type(e).__name__, flush=True)
    print("games", len(games), flush=True)
    pool = ThreadPoolExecutor(len(games) or 1)
    with open(out, "a") as fh:
        fh.write(json.dumps({"games": games}) + "\n")
        while time.time() < until:
            t = time.time()
            for r in pool.map(sample, games): fh.write(json.dumps(r) + "\n")
            fh.flush(); time.sleep(max(0.0, 3.0 - (time.time() - t)))
