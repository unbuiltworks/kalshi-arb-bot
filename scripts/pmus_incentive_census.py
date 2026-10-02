#!/usr/bin/env python3
"""P0/P2: current PMUS liquidity-incentive census from the authoritative API.

Source: GET https://gateway.polymarket.us/v1/incentives -- the same program
data as the authenticated api.polymarket.us/v1/incentives, served publicly by
the gateway the repo already uses for market data. Programs only; the
per-account `/v1/incentives/earnings` endpoint needs an API key and is
ACCOUNT-OBSERVED, so credited rewards are not obtainable here.

Everything written is SOURCE-OBSERVED with its fetch provenance. No modelling.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

GW = "https://gateway.polymarket.us/v1/incentives"
OUT = "data/pmus/incentives/programs.json"


def get(params):
    url = GW + "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(5):
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers={"Accept": "application/json"}),
                timeout=30)
            return json.load(r)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def main():
    progs, token, pages = [], None, 0
    while True:
        p = {"page_size": 500}
        if token:
            p["page_token"] = token
        payload = get(p)
        batch = payload.get("programs") or []
        progs.extend(batch)
        pages += 1
        token = payload.get("nextPageToken") or payload.get("next_page_token")
        if not token or not batch:
            break
        time.sleep(0.25)          # documented limit is 5 req/s
    out = {
        "source": GW,
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
        "count": len(progs),
        "programs": progs,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"))
    print(f"pages={pages} programs={len(progs)} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
