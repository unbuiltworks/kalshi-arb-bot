#!/usr/bin/env python3
"""PUBLISHED: walk the public PMUS liquidity-incentive program list.

READ-ONLY. GET https://gateway.polymarket.us/v1/incentives?pageSize=200[&pageToken=..]
(User-Agent "research-client", <= 2 req/s). No auth.

Guards against the earlier looping/duplicating walk:
  * dedupe rows by (marketSlug, programId, start);
  * stop when the next token was already used, when a page adds zero new
    keys, when there is no token, or at MAX_PAGES (reported as truncated).

Writes raw/incentives_walk.json.gz (deduped rows + walk log).

    python3 analysis/l5/pivot_pmus/incentive_walk.py [MAX_PAGES]
"""
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GW = "https://gateway.polymarket.us"
MAX_PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def get(path):
    req = urllib.request.Request(GW + path, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    rows, log, used = {}, [], set()
    token, stop = None, "max_pages"
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    for page in range(MAX_PAGES):
        path = "/v1/incentives?pageSize=200" + ("&pageToken=" + urllib.parse.quote(token) if token else "")
        t0 = time.time()
        try:
            d = get(path)
        except Exception as exc:   # noqa: BLE001
            stop = "error: %s %s" % (type(exc).__name__, str(exc)[:120]); break
        progs = d.get("programs") or []
        new = 0
        for p in progs:
            for tp in p.get("timePeriods") or [{}]:
                k = (p.get("marketSlug"), tp.get("programId"), tp.get("start"))
                if k not in rows:
                    rows[k] = {**{kk: vv for kk, vv in p.items() if kk != "timePeriods"}, "tp": tp}
                    new += 1
        nxt = d.get("nextPageToken") or d.get("pageToken") or d.get("next")
        log.append({"page": page, "programs": len(progs), "new_keys": new, "has_next": bool(nxt)})
        if page % 25 == 0:
            print(page, len(progs), new, len(rows), flush=True)
        if not nxt:
            stop = "no_next_token"; break
        if nxt in used:
            stop = "token_repeat"; break
        if new == 0:
            stop = "page_added_nothing"; break
        used.add(nxt); token = nxt
        time.sleep(max(0.0, 0.6 - (time.time() - t0)))
    out = {"label": "PUBLISHED program list (public GET), active-only per prior measurement",
           "started": started, "ended": dt.datetime.now(dt.timezone.utc).isoformat(),
           "stop_reason": stop, "pages": len(log), "unique_rows": len(rows),
           "log": log, "rows": list(rows.values())}
    json.dump(out, gzip.open(os.path.join(HERE, "raw", "incentives_walk.json.gz"), "wt"))
    print("stop:", stop, "pages", len(log), "unique rows", len(rows))


if __name__ == "__main__":
    main()
