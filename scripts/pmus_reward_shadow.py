#!/usr/bin/env python3
"""READ-ONLY per-second PMUS liquidity-reward shadow.

GET only. Imports no engine, no money path, no mandate, no authorization.
The only endpoint touched is /v1/markets/{slug}/book on the public gateway.
Nothing here can submit, cancel, arm or price an order.

Purpose: settle the ~400x discrepancy between the modeled instantaneous
reward share (up to $66 for q1) and the $0.16/rewarded-row our account has
actually been credited. A single snapshot cannot tell a persistent
under-supplied side from a transient one; one-second sampling across a
program period can.

What is retained per sample, so the scoring can be replayed exactly and
audited later without re-fetching: the full bid and offer ladders as the
venue returned them, the wall-clock, and the program parameters in force.
Scores are recomputed at analysis time from the raw ladders, never trusted
from this writer.

Venue rate limit is 5 requests/second per endpoint, so the market count is
capped below that and a slot that overruns its second is skipped rather than
queued -- a late sample is a wrong sample.
"""
from __future__ import annotations

import json, os, sys, time, urllib.request
from datetime import datetime, timezone

GW = "https://gateway.polymarket.us/v1"
OUT_DIR = "data/pmus/reward_shadow"
MAX_MARKETS = 4          # venue limit is 5 req/s on this endpoint
PERIOD_S = 1.0


def get_book(slug):
    r = urllib.request.urlopen(urllib.request.Request(
        f"{GW}/markets/{slug}/book", headers={"Accept": "application/json"}),
        timeout=8)
    return json.load(r)["marketData"]


def ladder(side):
    out = []
    for lv in side or []:
        try:
            out.append([lv["px"]["value"], lv["qty"]])
        except Exception:
            continue
    return out


def main(slugs, seconds, tag):
    os.makedirs(OUT_DIR, exist_ok=True)
    started = datetime.now(timezone.utc)
    path = os.path.join(
        OUT_DIR, f"shadow_{tag}_{started.strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    n_ok = n_err = 0
    deadline = time.time() + seconds
    with open(path, "w") as fh:
        fh.write(json.dumps({
            "kind": "header", "started_utc": started.isoformat(),
            "slugs": slugs, "seconds": seconds,
            "endpoint": f"{GW}/markets/{{slug}}/book",
            "read_only": True,
        }) + "\n")
        while time.time() < deadline:
            tick = time.time()
            for slug in slugs:
                try:
                    md = get_book(slug)
                    fh.write(json.dumps({
                        "kind": "book", "slug": slug,
                        "t": datetime.now(timezone.utc).isoformat(),
                        "bids": ladder(md.get("bids")),
                        "offers": ladder(md.get("offers")),
                        "state": md.get("state"),
                    }) + "\n")
                    n_ok += 1
                except Exception as e:
                    fh.write(json.dumps({
                        "kind": "error", "slug": slug,
                        "t": datetime.now(timezone.utc).isoformat(),
                        "error": str(e)[:200],
                    }) + "\n")
                    n_err += 1
            fh.flush()
            # a sample that could not be taken inside its second is DROPPED,
            # not deferred: the analysis counts seconds, so a shifted sample
            # would silently corrupt the qualifying-time fraction.
            sleep = PERIOD_S - (time.time() - tick)
            if sleep > 0:
                time.sleep(sleep)
    print(f"{path} ok={n_ok} err={n_err}")
    return 0


if __name__ == "__main__":
    secs = int(sys.argv[1]); tag = sys.argv[2]; slugs = sys.argv[3:]
    assert 1 <= len(slugs) <= MAX_MARKETS, "market count must respect the rate limit"
    sys.exit(main(slugs, secs, tag))
