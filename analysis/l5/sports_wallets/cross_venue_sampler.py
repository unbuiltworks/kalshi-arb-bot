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
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
        row.update(ti=round(t, 2), ib=bids[0][0] if bids else None, ia=asks[0][0] if asks else None,
                   ibq=bids[0][1] if bids else 0, iaq=asks[0][1] if asks else 0)
    except Exception as e: row["ie"] = type(e).__name__
    try:
        t = time.time(); d = get("https://gateway.polymarket.us/v1/markets/aec-%s/book" % g["slug"])["marketData"]
        ub = sorted(((float(x["px"]["value"]), float(x["qty"])) for x in d.get("bids") or []), reverse=True)
        ua = sorted((float(x["px"]["value"]), float(x["qty"])) for x in d.get("offers") or [])
        row.update(tu=round(t, 2), ub=ub[0][0] if ub else None, ua=ua[0][0] if ua else None, us=d.get("state"),
                   ubq=ub[0][1] if ub else 0, uaq=ua[0][1] if ua else 0, ub3=ub[:3], ua3=ua[:3],
                   ush=(d.get("stats") or {}).get("sharesTraded"))
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
