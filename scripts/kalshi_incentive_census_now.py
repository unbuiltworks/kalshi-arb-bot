"""CURRENT Kalshi incentive census from the public API. Read-only, no credentials.

The repo's existing census (analysis/l5/KALSHI_INCENTIVES_CENSUS.md) is dated
2026-08-31 and is treated as STALE. This refetches
`GET /trade-api/v2/incentive_programs` in full, with provenance, and reports what
is ACTIVE NOW.

Unit caution carried forward: `period_reward` is an integer whose unit is NOT
documented in the response. The prior census guessed centi-cents (200,000 -> $20)
against Kalshi's advertised $1-$1,000/market/day pools. That guess is NOT
promoted to fact here; both readings are reported and the unit is labelled
UNVERIFIED.

    python3 scripts/kalshi_incentive_census_now.py <out.json>
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = ("https://api.elections.kalshi.com/trade-api/v2/incentive_programs"
        "?limit=1000")
UA = {"User-Agent": "l5-kalshi-census/1.0 (read-only research)"}


def get(url):
    req = urllib.request.Request(url, headers=UA, method="GET")
    with urllib.request.urlopen(req, timeout=30) as fh:
        return json.loads(fh.read().decode("utf-8")), fh.status


def main(argv):
    out = argv[1] if len(argv) > 1 else "data/kalshi/census/incentive_programs.json"
    progs, cursor, pages = [], None, 0
    while True:
        url = BASE + (f"&cursor={cursor}" if cursor else "")
        body, status = get(url)
        got = body.get("incentive_programs") or []
        progs.extend(got)
        pages += 1
        cursor = body.get("next_cursor") or None
        if not cursor or not got:
            break
        if pages % 10 == 0:
            print(f"  {pages} pages, {len(progs)} programs", flush=True)
        time.sleep(0.1)
    rec = {"source": BASE, "fetched_utc": datetime.now(timezone.utc).isoformat(),
           "pages": pages, "count": len(progs), "programs": progs}
    json.dump(rec, open(out, "w"))
    print(f"fetched {len(progs)} programs over {pages} pages -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
