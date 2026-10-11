"""WHICH ORDER-STATE ENDPOINTS DOES PMUS ACTUALLY SERVE? Read-only.

The requirement-7 verification found that `GET /v1/orders?limit=100` returns
**HTTP 501 Not Implemented** on the account host, while `GET /v1/orders/open`
returns 200. My repaired `order_state` reader requires BOTH, so it fails
closed on every single read -- safe, and unusable.

I wrote that reader against two paths I had seen named in earlier scripts
without confirming the venue serves them. This probe establishes, from the
venue, which paths exist and what they return, so the fix is built on
evidence instead of a second guess.

It also reconciles the 1215z window, whose ledger records twenty placements
that no longer appear in open orders.

GETs only, over `pmus_auth.ReadOnlyTransport`.
"""
from __future__ import annotations

import json
import time
from typing import Any

from src import pmus_auth as A

CANDIDATES = (
    "/v1/orders/open",
    "/v1/orders",
    "/v1/orders?limit=100",
    "/v1/orders?limit=100&status=all",
    "/v1/orders/history",
    "/v1/orders/history?limit=100",
    "/v1/orders/closed",
    "/v1/mytrades",
    "/v1/mytrades?limit=100",
    "/v1/trades",
    "/v1/fills",
    "/v1/portfolio/positions",
    "/v1/portfolio/activities?limit=50",
    "/v1/account/balances",
)

REDACT = ("accountId", "account_id", "walletAddress", "address", "userId",
          "subaccountId", "ownerId")


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def shape(body: Any) -> Any:
    if isinstance(body, dict):
        return {k: ("list[%d]" % len(v) if isinstance(v, list)
                    else type(v).__name__) for k, v in body.items()}
    if isinstance(body, list):
        return "list[%d]" % len(body)
    return type(body).__name__


def main() -> int:
    t = A.ReadOnlyTransport()
    print("PROBE_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    for path in CANDIDATES:
        a = time.time()
        try:
            body = t.request("GET", path)
            print("EP %s" % json.dumps({
                "path": path, "status": 200,
                "latency_s": round(time.time() - a, 4),
                "shape": shape(body),
                "sample": json.dumps(scrub(body), default=str)[:400],
            }, default=str))
        except Exception as exc:                               # noqa: BLE001
            print("EP %s" % json.dumps({
                "path": path, "error": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "latency_s": round(time.time() - a, 4),
            }, default=str))

    # the 1215z reconciliation: twenty placements in the ledger, nothing in
    # open orders. Trades and positions are the authority on whether any of
    # them became inventory.
    for path in ("/v1/mytrades?limit=100", "/v1/portfolio/positions",
                 "/v1/account/balances"):
        try:
            body = t.request("GET", path)
        except Exception as exc:                               # noqa: BLE001
            print("W1215 %s ERR %s" % (path, type(exc).__name__))
            continue
        s = json.dumps(scrub(body), default=str)
        for marker in ("1215z", "1230z"):
            print("W1215 %s %s occurrences=%d"
                  % (path, marker, s.count(marker)))
        print("W1215 %s len=%d head=%s" % (path, len(s), s[:300]))
    print("PROBE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
