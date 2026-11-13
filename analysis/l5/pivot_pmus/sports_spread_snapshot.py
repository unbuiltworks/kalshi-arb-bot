#!/usr/bin/env python3
"""SNAPSHOT: top-of-book spread on currently open PMUS sports game markets.

READ-ONLY. Public, unauthenticated GETs on https://gateway.polymarket.us only
(User-Agent "research-client", <= 2 requests/second). No order, no credential.

1. Reads the event listing already fetched to raw/sports_events_all.json.gz
   (GET /v1/events?active=true&closed=false&categories=sports&limit=500&offset=N).
2. Picks up to N_MAX game markets (no futures): every live event (one market
   each, round-robin over leagues), then pre-game events starting within
   PRE_HOURS hours, round-robin over leagues, soonest first.
3. Takes ROUNDS snapshots of GET /v1/markets/{slug}/book, round starts spaced
   SPACING_S seconds apart, and records best bid / best ask / size at touch /
   depth within 1c and 3c on each side.

A PMUS binary market has one book (the YES/long side); the NO side's best bid
is 1 - YES ask, so the spread is the same number on both sides.

    python3 analysis/l5/pivot_pmus/sports_spread_snapshot.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
GW = "https://gateway.polymarket.us"
N_MAX, ROUNDS, SPACING_S, PRE_HOURS = 60, 3, 60.0, 12.0
MIN_GAP = 0.55   # seconds between requests (< 2 req/s)


def get(path):
    req = urllib.request.Request(GW + path, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def px(l):
    v = l.get("px")
    return float(v.get("value") if isinstance(v, dict) else v)


def summarize(md):
    bids = sorted(((px(l), float(l.get("qty") or 0)) for l in md.get("bids") or []), reverse=True)
    offs = sorted((px(l), float(l.get("qty") or 0)) for l in md.get("offers") or [])
    out = {"state": md.get("state"), "n_bid_lvls": len(bids), "n_ask_lvls": len(offs)}
    if bids:
        out.update(bid=bids[0][0], bid_qty=bids[0][1],
                   bid_depth_1c=sum(q for p, q in bids if p >= bids[0][0] - 0.0100001),
                   bid_depth_3c=sum(q for p, q in bids if p >= bids[0][0] - 0.0300001))
    if offs:
        out.update(ask=offs[0][0], ask_qty=offs[0][1],
                   ask_depth_1c=sum(q for p, q in offs if p <= offs[0][0] + 0.0100001),
                   ask_depth_3c=sum(q for p, q in offs if p <= offs[0][0] + 0.0300001))
    if bids and offs:
        out["spread"] = round(offs[0][0] - bids[0][0], 4)
    return out


def select(events):
    now = dt.datetime.now(dt.timezone.utc)
    live, pre = defaultdict(list), defaultdict(list)
    for e in events:
        ms = [m for m in e.get("markets") or []
              if m.get("sportsMarketType") != "futures" and not m.get("closed")]
        if not ms:
            continue
        m = ms[0]
        st = m.get("gameStartTime") or e.get("startTime")
        try:
            t = dt.datetime.fromisoformat(st.replace("Z", "+00:00"))
        except Exception:
            continue
        h = (t - now).total_seconds() / 3600
        lg = m["slug"].split("-")[1]
        rec = {"slug": m["slug"], "league": lg, "sport": (m.get("sportsMarketType") or "").split("_")[0],
               "event": e.get("slug"), "period": e.get("period"), "hours_to_start": round(h, 2),
               "tick": m.get("orderPriceMinTickSize")}
        if e.get("live"):
            rec["phase"] = "in-play"; live[lg].append(rec)
        elif 0 < h <= PRE_HOURS:
            rec["phase"] = "pre-game"; pre[lg].append(rec)
    for d in (live, pre):
        for lg in d:
            d[lg].sort(key=lambda r: r["hours_to_start"])
    pick = []
    for d, cap in ((live, 25), (pre, N_MAX)):
        added = 0
        while any(d.values()) and len(pick) < N_MAX and added < cap:
            for lg in sorted(d, key=lambda k: (-len(d[k]), k)):
                if d[lg] and len(pick) < N_MAX and added < cap:
                    pick.append(d[lg].pop(0)); added += 1
    return pick


def main():
    ev = json.load(gzip.open(os.path.join(RAW, "sports_events_all.json.gz")))
    sample = select(ev["events"])
    print("selected %d markets (%d in-play)" % (len(sample), sum(r["phase"] == "in-play" for r in sample)))
    snaps = []
    last = 0.0
    for rnd in range(ROUNDS):
        t0 = time.time()
        for r in sample:
            wait = MIN_GAP - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.time()
            row = {"round": rnd, "slug": r["slug"],
                   "t": dt.datetime.now(dt.timezone.utc).isoformat()}
            try:
                row.update(summarize(get("/v1/markets/%s/book" % r["slug"]).get("marketData") or {}))
            except Exception as exc:   # noqa: BLE001
                row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            snaps.append(row)
        print("round %d done in %.0fs" % (rnd, time.time() - t0), flush=True)
        if rnd < ROUNDS - 1:
            rest = SPACING_S - (time.time() - t0)
            if rest > 0:
                time.sleep(rest)
    out = {"label": "SNAPSHOT, public PMUS order books, read-only",
           "events_fetched_at": ev.get("fetched_at"), "endpoint": "/v1/markets/{slug}/book",
           "sample": sample, "snapshots": snaps}
    json.dump(out, open(os.path.join(RAW, "sports_book_snapshots.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
