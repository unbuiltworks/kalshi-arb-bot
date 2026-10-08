"""RECONCILE THE 1215z WINDOW. Read-only.

The endpoint probe found `/v1/portfolio/activities` returns an
`ACTIVITY_TYPE_POSITION_RESOLUTION` naming
`cpc-btc-updown-15m-2026-10-08-1215z`. A position resolution means WE HELD A
POSITION in that market -- which means at least one of the twenty 1215z
first legs FILLED. The cohort ledger records 40 placements across 1215z and
1230z; only the 1230z twenty were ever audited.

Order history is unavailable on this venue (`/v1/orders` is HTTP 501,
`/v1/mytrades` and `/v1/fills` are 404), so activities and balances are the
authority. This pages activities and prints every row that names a BTC15m
market, in full, plus the resolution arithmetic.

GETs only, in the readonly-recovery image.
"""
from __future__ import annotations

import json
import time
from typing import Any

from src import pmus_auth as A

REDACT = ("accountId", "account_id", "walletAddress", "address", "userId",
          "subaccountId", "ownerId")
PAGES = 12
PAGE = 50


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def main() -> int:
    t = A.ReadOnlyTransport()
    print("RECON_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    cursor = ""
    seen = 0
    kinds: dict = {}
    for p in range(PAGES):
        path = "/v1/portfolio/activities?limit=%d" % PAGE
        if cursor:
            path += "&cursor=%s" % cursor
        try:
            body = t.request("GET", path)
        except Exception as exc:                               # noqa: BLE001
            print("PAGE_ERR %s" % json.dumps({
                "page": p, "error": type(exc).__name__,
                "code": getattr(exc, "code", None)}, default=str))
            break
        rows = (body or {}).get("activities") or []
        for r in rows:
            seen += 1
            k = str(r.get("type"))
            kinds[k] = kinds.get(k, 0) + 1
            s = json.dumps(scrub(r), default=str)
            if "btc-updown-15m-2026-10-0" in s:
                print("ACT %s" % s[:900])
        cursor = (body or {}).get("nextCursor") or ""
        if (body or {}).get("eof") or not cursor:
            break

    print("ACT_SUMMARY %s" % json.dumps({
        "rows_scanned": seen, "kinds": kinds}, default=str))

    for path in ("/v1/orders/open", "/v1/portfolio/positions",
                 "/v1/account/balances"):
        try:
            print("NOW %s %s" % (path, json.dumps(
                scrub(t.request("GET", path)), default=str)[:600]))
        except Exception as exc:                               # noqa: BLE001
            print("NOW %s ERR %s" % (path, type(exc).__name__))
    print("RECON_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
