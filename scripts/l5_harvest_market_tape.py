#!/usr/bin/env python3
"""Harvest the PUBLIC market-wide Polymarket trade tape for a preregistered
sample of L5's 2026-08-31 windows. READ-ONLY, public endpoint, no credentials,
no order. This is the only causally-prior market state obtainable for the
maker/taker-labelled corpus.

Sample rule, fixed before any result is computed: the 2026-08-31 windows with
the MOST L5 fills, taken in descending fill count, first N. Chosen for label
density, not for outcome -- no price, settlement or P&L enters the selection.
"""
from __future__ import annotations
import csv, gzip, json, os, sys, time, urllib.request, collections

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
OUT = "data/l5_market_tape"
API = "https://data-api.polymarket.com/trades"


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1:
                print(f"    FAIL {e}")
                return None
            time.sleep(2 ** i)


R = list(csv.DictReader(gzip.open("analysis/l5/l5_canonical_sequence.csv.gz", "rt")))
A = [r for r in R if r["datetime_utc"][:10] == "2026-08-31"]
cnt = collections.Counter(r["window_id"] for r in A)
sample = [w for w, _ in cnt.most_common(N)]
slug = {r["window_id"]: r["market_slug"] for r in A}
print(f"08-31 windows: {len(cnt)}; sampling top {len(sample)} by L5 fill count "
      f"({cnt[sample[0]]}..{cnt[sample[-1]]} fills)")

man = []
for i, cid in enumerate(sample, 1):
    path = f"{OUT}/{cid}.jsonl.gz"
    if os.path.exists(path):
        print(f"[{i}/{len(sample)}] cached {slug[cid]}")
        continue
    rows, off = [], 0
    while off <= 10000:
        d = get(f"{API}?market={cid}&limit=500&offset={off}")
        if not isinstance(d, list) or not d:
            break
        rows += d
        if len(d) < 500:
            break
        off += 500
    with gzip.open(path, "wt") as fh:
        for x in rows:
            fh.write(json.dumps(x) + "\n")
    ts = [int(x["timestamp"]) for x in rows] or [0]
    man.append({"window_id": cid, "slug": slug[cid], "rows": len(rows),
                "wallets": len({x["proxyWallet"] for x in rows}),
                "t_min": min(ts), "t_max": max(ts), "l5_fills_in_corpus": cnt[cid]})
    print(f"[{i}/{len(sample)}] {slug[cid]} rows={len(rows)} "
          f"wallets={len({x['proxyWallet'] for x in rows})}")
    time.sleep(0.25)
json.dump({"source": API, "provenance": "PUBLIC market-wide trade tape, read-only, no credentials",
           "sample_rule": "2026-08-31 windows ranked by L5 fill count, descending, top N; "
                          "no price/settlement/P&L in the selection",
           "n": len(sample), "markets": man},
          open(f"{OUT}/manifest.json", "w"), indent=1)
print(f"\nwrote {OUT}/manifest.json")
