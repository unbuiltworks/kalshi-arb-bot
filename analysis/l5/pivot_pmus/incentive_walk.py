#!/usr/bin/env python3
"""PUBLISHED: census of PMUS liquidity-incentive programs for every open market.

READ-ONLY. Public GETs on https://gateway.polymarket.us (User-Agent
"research-client", <= 2 req/s). No auth.

WHY NOT A pageToken WALK. Measured 2026-11-13 ~16:10Z: GET /v1/incentives
serves 100 rows per page and its nextPageToken returns page 1 again (the
token decodes to the same cursor key with a new timestamp), with or without
a category filter; offset/page/limit are ignored. A token walk therefore
loops on the same 100 rows -- the cause of the earlier "looped/duplicated"
200-page walk, and why the 40-page walk in PMUS_REBATES_INCENTIVES_20261113.md
saw only AI/tech/finance rows. `category=` (FIN, TECH, MAC, POL, CUL, CRY,
SPR, CLI, GEO, SCI) filters but is still capped at 100 rows.

WHAT WORKS. Repeated `symbols=<slug>` parameters (comma-joined returns
nothing) return the programs for exactly those markets. So:
  1. raw/all_events_slim.json.gz = every open market slug
     (GET /v1/events?active=true&closed=false&limit=500&offset=N)
  2. query /v1/incentives with BATCH slugs per request;
     a batch that returns >= 100 programs is split and re-queried (cap guard);
  3. dedupe by (marketSlug, programId, start).

    python3 analysis/l5/pivot_pmus/incentive_walk.py
"""
import datetime as dt
import gzip
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GW = "https://gateway.polymarket.us"
BATCH = 60
MIN_GAP = 0.55
_last = [0.0]


def get(path):
    w = MIN_GAP - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()
    req = urllib.request.Request(GW + path, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def query(slugs, rows, log):
    d = get("/v1/incentives?" + "&".join("symbols=" + s for s in slugs))
    progs = d.get("programs") or []
    if len(progs) >= 100 and len(slugs) > 1:     # possible cap: split
        h = len(slugs) // 2
        query(slugs[:h], rows, log); query(slugs[h:], rows, log)
        return
    log.append({"n_slugs": len(slugs), "programs": len(progs)})
    for p in progs:
        for tp in p.get("timePeriods") or [{}]:
            k = (p.get("marketSlug"), tp.get("programId"), tp.get("start"))
            rows.setdefault(k, {**{kk: vv for kk, vv in p.items() if kk != "timePeriods"}, "tp": tp})


def main():
    ev = json.load(gzip.open(os.path.join(HERE, "raw", "all_events_slim.json.gz")))
    cat = {}
    for e in ev["events"]:
        for m in e["markets"]:
            if not m.get("closed"):
                cat[m["slug"]] = e.get("category")
    slugs = sorted(cat)
    rows, log, errors = {}, [], []
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(0, len(slugs), BATCH):
        try:
            query(slugs[i:i + BATCH], rows, log)
        except Exception as exc:   # noqa: BLE001
            errors.append({"batch_start": i, "error": "%s %s" % (type(exc).__name__, str(exc)[:100])})
        if (i // BATCH) % 50 == 0:
            print(i, len(slugs), len(rows), flush=True)
    for r in rows.values():
        r["event_category"] = cat.get(r.get("marketSlug"))
    out = {"label": "PUBLISHED program list (public GET /v1/incentives?symbols=...), active-only",
           "events_fetched_at": ev.get("fetched_at"), "started": started,
           "ended": dt.datetime.now(dt.timezone.utc).isoformat(),
           "open_markets_queried": len(slugs), "requests": len(log), "errors": errors,
           "unique_rows": len(rows), "rows": list(rows.values())}
    json.dump(out, gzip.open(os.path.join(HERE, "raw", "incentives_census.json.gz"), "wt"))
    print("done: markets", len(slugs), "requests", len(log), "rows", len(rows), "errors", len(errors))


if __name__ == "__main__":
    main()
