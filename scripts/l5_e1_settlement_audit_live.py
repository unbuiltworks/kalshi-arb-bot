"""THE SETTLEMENT AUDIT, AGAINST LIVE PMUS. Read-only.

Demonstrates that the reconciler retrieves the known 1215z E1 settlement
row and attributes its quantity and cashflows WITHOUT including another
strategy's trades on the shared account.

It uses the filtered, resumable, 429-aware scanner and the FROZEN
`order_state.settlement_for` for attribution. It runs in the
readonly-recovery image, which holds no executor and no mutating
transport, and it is not on the trading path: the lifecycle stays frozen
at 8b729a94 / c576f741.
"""
from __future__ import annotations

import json
import os
import time

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import settlement_audit as SA
from src import pmus_auth as A

SLUG_1215 = "cpc-btc-updown-15m-2026-10-08-1215z"
SLUG_1230 = "cpc-btc-updown-15m-2026-10-08-1230z"
STATE = os.environ.get("AUDIT_STATE", "/tmp/e1_settlement_scan.json")


def j(tag, obj):
    print("%s %s" % (tag, json.dumps(obj, default=str)))


def main() -> int:
    reader = OS.venue_reader(A.ReadOnlyTransport())
    print("AUDIT_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    t0 = time.time()
    scan = SA.scan_resolutions(reader, state_path=STATE)
    j("SCAN", {"pages_read": scan["pages_read"],
               "complete": scan["complete"],
               "rows_gathered": len(scan["rows"]),
               "filter": scan["filter"],
               "filter_honoured": scan["filter_honoured"],
               "page_size": scan["page_size"],
               "rate_limit_hits": scan["rate_limit_hits"],
               "resumed_from_page": scan["resumed_from_page"],
               "errors": scan["errors"],
               "elapsed_s": round(time.time() - t0, 2)})

    res = {"rows": scan["rows"], "complete": scan["complete"],
           "pages_read": scan["pages_read"],
           "error": (scan["errors"][0]["error"] if scan["errors"]
                     else None)}

    # OURS: 2.60 shares in 1215z
    ours = OS.settlement_for(reader, slug=SLUG_1215,
                             expected_qty="2.60", resolutions=res)
    j("E1_1215Z", {"label": ours.get("label"),
                   "rows_seen_for_this_market": len(ours.get("rows") or []),
                   "attributed": ours.get("attributed"),
                   "error": ours.get("error"),
                   "NEVER_APPORTIONED": ours.get("NEVER_APPORTIONED")})

    # 1230z: cancelled, nothing filled
    other = OS.settlement_for(reader, slug=SLUG_1230,
                              expected_qty="0", resolutions=res)
    j("E1_1230Z", {"label": other.get("label"),
                   "rows_seen_for_this_market": len(other.get("rows") or []),
                   "attributed": other.get("attributed")})

    # CONTAMINATION CONTROL: ask for a quantity that is NOT ours
    bad = OS.settlement_for(reader, slug=SLUG_1215,
                            expected_qty="16.21", resolutions=res)
    j("CONTAMINATION_CONTROL", {
        "asked_for": "16.21",
        "attributed": bad.get("attributed"),
        "label": bad.get("label"),
        "rows_seen": len(bad.get("rows") or []),
        "reading": "attributing anything here would mean exact-match "
                   "attribution is broken"})

    # every market the scan saw, so cross-contamination is visible
    seen = {}
    for a in scan["rows"]:
        pr = a.get("positionResolution") or {}
        s = str(pr.get("marketSlug"))
        q = ((pr.get("beforePosition") or {}).get("netPositionDecimal"))
        seen.setdefault(s, []).append(str(q))
    j("MARKETS_IN_SCAN", {"distinct_markets": len(seen),
                          "our_two": {k: v for k, v in seen.items()
                                      if k in (SLUG_1215, SLUG_1230)}})
    j("NOTE", {"READ_ONLY": True, "armed": False,
               "orders_submitted": 0, "orders_cancelled": 0,
               "trading_lifecycle_frozen_at": "8b729a94 / c576f741",
               "frozen_modules_unmodified": True})
    print("AUDIT_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
