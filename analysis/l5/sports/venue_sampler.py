"""Read-only: PMUS and international books for two-outcome markets, every ~3 s.
usage: venue_sampler.py <out.jsonl> <until_epoch> <pmus_slug>=<intl_slug> ..."""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
G = "https://gateway.polymarket.us"
def get(u, t=10):
    r = urllib.request.Request(u, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as f:
        return json.load(f)
out, until = sys.argv[1], float(sys.argv[2])
pairs = []          # (pmus_slug, intl_slug)
for a in sys.argv[3:]:
    p, i = a.split("=")
    pairs.append((p, i))
toks = {}
for p, i in pairs:
    m = get("https://gamma-api.polymarket.com/markets?slug=" + i)
    assert m and m[0]["slug"] == i and len(json.loads(m[0]["outcomes"])) == 2, i
    # token 0 = the first-named outcome (the first team in the slug) = PMUS YES
    toks[p] = json.loads(m[0]["clobTokenIds"])[0]
def one(p):
    row = {"t": time.time(), "slug": p}
    try:
        md = get(G + "/v1/markets/%s/book" % p)["marketData"]
        b = sorted(((float(x["px"]["value"]), float(x["qty"])) for x in md.get("bids") or []), reverse=True)
        a = sorted(((float(x["px"]["value"]), float(x["qty"])) for x in md.get("offers") or []))
        row.update(us=md.get("state"), ub=b[0][0] if b else None, ubq=b[0][1] if b else None,
                   ua=a[0][0] if a else None, uaq=a[0][1] if a else None,
                   ub3=sum(q for _, q in b[:3]), ua3=sum(q for _, q in a[:3]),
                   shares=(md.get("stats") or {}).get("sharesTraded"))
    except Exception as e:
        row["uerr"] = type(e).__name__
    try:
        bk = get("https://clob.polymarket.com/book?token_id=" + toks[p])
        bb = max(((float(x["price"]), float(x["size"])) for x in bk.get("bids") or []), default=None)
        ba = min(((float(x["price"]), float(x["size"])) for x in bk.get("asks") or []), default=None)
        row.update(ib=bb[0] if bb else None, ibq=bb[1] if bb else None,
                   ia=ba[0] if ba else None, iaq=ba[1] if ba else None)
    except Exception as e:
        row["ierr"] = type(e).__name__
    return row
ex = ThreadPoolExecutor(6)
with open(out, "a") as f:
    while time.time() < until:
        t0 = time.time()
        for row in ex.map(one, [p for p, _ in pairs]):
            f.write(json.dumps(row) + "\n")
        f.flush()
        time.sleep(max(0.2, 3.0 - (time.time() - t0)))
