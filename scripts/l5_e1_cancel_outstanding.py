"""CANCEL E1'S OUTSTANDING ORDERS. Cancel-and-verify only. Places nothing.

WHY. The hard-coded `our_outstanding_orders: 0` in `pmus_provider` means E1's
declared one-outstanding-order rule was never enforceable, so the engine
opened a new first leg every iteration until the 20/window attempt cap. The
venue confirms 20 resting 0.13-share NO orders on
`cpc-btc-updown-15m-2026-10-08-1230z`, all ORDER_STATE_NEW, zero filled.

E1 was disarmed and its process stopped, which means ITS OWN DEADMAN NEVER
RAN. A disarm does not cancel resting orders -- so they are cancelled here,
explicitly, with the APPROVED mechanism and no substitute:

    analysis.l5.live_exec2.orders.deadman_cancel_all
      -> POST /v1/orders/open/cancel  {"slugs": [slug]}

the same market-scoped deadman XR2W A6 used in its finally block. It is never
retried, per that module's own contract.

THEN IT VERIFIES AGAINST THE VENUE rather than trusting the response: it
re-reads GET /v1/orders/open and reports what is still live, because a cancel
response is a claim and the open-orders list is the fact.

WHAT IT CANNOT DO. It never builds an order body, never calls
`submit_one`/`submit_first_leg`, never touches a latch slot, never reads an
arming key and never advances the cohort. `BoundedOrderTransport` permits only
the four routes its own module allows, and the only mutating one used here is
the deadman. It does not need E1 to be armed -- refusing to cancel because a
flag is unset is the dangerous direction, as that module says.

Set CANCEL_SLUGS to a comma-separated slug list. Without it, nothing happens.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from analysis.l5.live_exec2 import orders as O
from src import pmus_auth as A

SLUGS = [s.strip() for s in os.environ.get("CANCEL_SLUGS", "").split(",")
         if s.strip()]


def open_orders(t) -> Dict[str, Any]:
    """The FACT: what the venue still shows as live."""
    try:
        body = t.request("GET", "/v1/orders/open")
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": "%s" % type(exc).__name__}
    rows = (body or {}).get("orders") or []
    return {"ok": True, "n": len(rows),
            "ids": [r.get("id") for r in rows][:40],
            "by_slug": {s: sum(1 for r in rows
                               if str(r.get("marketSlug")) == s)
                        for s in set(str(r.get("marketSlug"))
                                     for r in rows)}}


def main() -> int:
    out: Dict[str, Any] = {
        "probe": "E1_CANCEL_OUTSTANDING",
        "mechanism": "analysis.l5.live_exec2.orders:deadman_cancel_all",
        "route": O.DEADMAN_PATH,
        "places_no_orders": True,
        "touches_no_latch": True,
        "advances_no_window": True,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "slugs_requested": SLUGS,
    }
    if not SLUGS:
        out["REFUSED"] = "CANCEL_SLUGS is empty; nothing to cancel"
        print("CANCEL:" + json.dumps(out, default=str))
        return 0

    read = A.ReadOnlyTransport()
    out["open_before"] = open_orders(read)

    out["cancels"] = []
    for slug in SLUGS:
        t = O.BoundedOrderTransport()
        res = O.deadman_cancel_all(t, slug=slug)
        out["cancels"].append({"slug": slug, "result": res,
                               "NEVER_RETRIED": True})

    time.sleep(3.0)
    out["open_after"] = open_orders(read)

    before = (out["open_before"] or {}).get("n")
    after = (out["open_after"] or {}).get("n")
    out["verdict"] = {
        "open_before": before, "open_after": after,
        "all_cancelled": (after == 0) if isinstance(after, int) else None,
        "VERIFIED_AGAINST": "GET /v1/orders/open",
        "NOTE": ("a cancel response is a claim; the open-orders list is the "
                 "fact. If open_after is not 0, orders REMAIN LIVE."),
    }
    print("CANCEL:" + json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
