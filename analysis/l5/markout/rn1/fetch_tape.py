"""Step 2. Public trade tape per sampled condition: data-api /v2/trades?condition_id=..&limit=1000 (default takerOnly=true:
one row per taker fill, by anyone). Newest first, cursor-only; the endpoint has no time filter, so each condition is paged
back until older than the trader's earliest sampled fill (minus 20 min) or the page cap. <= 4 requests/second in total.
usage: fetch_tape.py [max_pages_per_condition=25]
Row stored: [timestamp, token_index, price, +1 taker BUY / -1 taker SELL, shares, tx_hash[:14]]"""
import concurrent.futures as cf, gzip, json, sys
from common import *
cap = int(sys.argv[1]) if len(sys.argv) > 1 else 25
S = json.load(open(SAMPLE)); os.makedirs(TAPE_DIR, exist_ok=True)
first = {}
for f in S["fills"]: first[f["c"]] = min(first.get(f["c"], 9e18), f["ts"])
def work(c):
    p = os.path.join(TAPE_DIR, c + ".json.gz")
    if os.path.exists(p): return c, None
    toks = S["markets"][c]["tokens"]; need = first[c] - 1200
    rows, cursor, pages, complete = [], None, 0, False
    while True:
        u = "https://data-api.polymarket.com/v2/trades?condition_id=%s&limit=1000" % c + ("&cursor=" + cursor if cursor else "")
        d = get(u)
        if not d or "data" not in d: return c, "FAILED after %d pages" % pages
        pages += 1
        for r in d["data"]:
            if r["token_id"] in toks:
                rows.append([r["timestamp"], toks.index(r["token_id"]), r["price"], 1 if r["side"] == "BUY" else -1, r["size"], r["transaction_hash"][:14]])
        pg = d.get("pagination") or {}; cursor = pg.get("next_cursor")
        if not d["data"] or not pg.get("has_more") or not cursor: complete = True; break
        if d["data"][-1]["timestamp"] < need: complete = True; break
        if pages >= cap: break
    json.dump({"pages": pages, "covers_from": rows[-1][0] if rows else None, "complete": complete, "rows": rows}, gzip.open(p, "wt"))
    return c, "pages %d rows %d complete %s" % (pages, len(rows), complete)
todo = sorted(first); n = 0
with cf.ThreadPoolExecutor(4) as ex:
    for c, msg in ex.map(work, todo):
        n += 1
        if msg and (n % 20 == 0 or "FAILED" in msg or "False" in msg): print(n, len(todo), c[:10], S["markets"][c]["slug"], msg, flush=True)
print("DONE")
