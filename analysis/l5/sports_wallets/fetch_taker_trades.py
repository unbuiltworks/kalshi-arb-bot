"""Pull one wallet's public trades from data-api /v2/trades, newest first, back to a cut-off.
READ-ONLY public data. The endpoint's default (takerOnly=true) returns only trades where the wallet
was the TAKER; takerOnly=false returns all its trades. Joining the taker-only rows to the activity
feed on (transaction_hash, token_id) flags each activity TRADE as taker; the rest are maker.
usage: fetch_taker_trades.py <wallet> <out.jsonl> <stop_before_utc_iso> [true|false]"""
import datetime as dt, json, sys, time, urllib.parse, urllib.request
W, out = sys.argv[1], sys.argv[2]
stop = dt.datetime.fromisoformat(sys.argv[3].replace("Z", "+00:00")).timestamp()
taker = sys.argv[4] if len(sys.argv) > 4 else "true"
KEEP = ("timestamp", "side", "size", "price", "condition_id", "token_id", "outcome_index", "slug", "transaction_hash")
cursor, n, pages = None, 0, 0
with open(out, "w") as fh:
    while True:
        q = {"user": W, "limit": 1000, "takerOnly": taker}
        if cursor: q["cursor"] = cursor
        url = "https://data-api.polymarket.com/v2/trades?" + urllib.parse.urlencode(q)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research-client"}), timeout=40) as r:
                    doc = json.loads(r.read().decode())
                break
            except Exception as exc:
                print("retry", attempt, type(exc).__name__, flush=True); time.sleep(3 * (attempt + 1))
        else:
            print("GAVE_UP pages", pages); sys.exit(1)
        rows = doc.get("data") or []; pages += 1; done = False
        for r in rows:
            if r["timestamp"] < stop: done = True; break
            fh.write(json.dumps({k: r.get(k) for k in KEEP}, separators=(",", ":")) + "\n"); n += 1
        pg = doc.get("pagination") or {}; cursor = pg.get("next_cursor")
        if done or not rows or not pg.get("has_more") or not cursor: break
        time.sleep(1.0)
print("DONE pages", pages, "rows", n, "oldest", rows[-1]["timestamp"] if rows else None)
