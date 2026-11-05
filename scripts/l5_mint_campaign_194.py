#!/usr/bin/env python3
"""Mint the issue-194 two-hour bounded campaign document, AT INSTALL TIME.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT PASTED INTO A REPORT. This program
has lost at least four campaigns to the same operational failure: a document
is minted, reported for approval, and has aged past its bound by the time the
owner approves it ("The reported authorization has expired, so do not
activate or reuse it"). `issued_utc` is what ages, so the only fix is to stamp
it when the variable is actually installed -- not when the proposal is
written. Run this, then install what it prints, in the same sitting.

IT INSTALLS NOTHING AND IT TRADES NOTHING. It prints a document, its campaign
id and its SHA256, and validates it against the REAL approval gate read-only.
Installing the variable is the owner's action and is the approval.

READ-ONLY VALIDATION, DELIBERATELY. It calls `ApprovalGate.state()`, never
`authorize_window`, because `authorize_window` RESERVES a window -- a
verification that consumed the very window it was verifying has already
happened once in this program and must not happen again.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_campaign_approval as CA          # noqa: E402
from src import l5_live_authorization as AUTH       # noqa: E402

#: THE OWNER'S AGREED SCOPE, issue #194 comment 2026-11-05T20:20:35Z:
#: "500 total submissions (not 500 simultaneous orders), 2 concurrent resting
#:  orders (one each side), $20 total exposure, $12 deployed cash, $5 daily
#:  realized-loss halt; repricing and completion allowed within limits."
#:
#: EVERY ONE OF THESE IS AT OR INSIDE A CODE CEILING. Nothing is widened:
#:   max_windows 8            == CA.MAX_WINDOWS (8)        -> 8 x 15m = 2h
#:   max_total_exposure_usd 20 == CA.MAX_TOTAL_EXPOSURE_USD
#:   max_daily_loss_usd 5      == CA.MAX_DAILY_LOSS_USD
#:   series                    in CA.ALLOWED_SERIES
#:   duration 2h              <= CA.MAX_CAMPAIGN_S (4h)
WINDOWS = 8
DURATION_S = 2 * 3600
EXPOSURE_USD = "20"
DAILY_LOSS_USD = "5"
CASH_USD = "12"

#: THE CLASS SPLIT, and the reserve is why it is not even.
#: `COMPLETION_SUBMISSIONS_PER_OBLIGATION` is 2 -- a complement plus one
#: re-derivation after a partial fill or a book that moved. So COMPLETE must
#: hold at least 2 per OPEN or `completion_capacity_refusal` will (correctly)
#: refuse to open exposure the completion budget could no longer close.
#:   OPEN 140 -> needs 280 COMPLETE; 300 is granted, 20 spare.
#: They sum to exactly the 500 total the owner set, so the flat backstop and
#: the class budgets bind at the same place.
MAX_SUBMISSIONS = 500
CLASS_BUDGETS = {"OPEN": 140, "COMPLETE": 300, "REPLACE": 60}


def mint(now: dt.datetime | None = None, nonce: str | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    nonce = nonce or secrets.token_hex(16)
    return {
        "approved": True,
        "nonce": nonce,
        "series": CA.ALLOWED_SERIES[0],
        "issued_utc": now.replace(microsecond=0).isoformat(),
        "expires_utc": (now + dt.timedelta(seconds=DURATION_S)
                        ).replace(microsecond=0).isoformat(),
        "max_windows": WINDOWS,
        "max_total_exposure_usd": EXPOSURE_USD,
        "max_daily_loss_usd": DAILY_LOSS_USD,
        "note": ("issue-194 two-hour bounded L5 PMUS campaign; "
                 "500 submissions, 2 resting orders, $12 deployed cash"),
    }


def validate(doc: dict) -> dict:
    """Run the document past the REAL gate, read-only, in a throwaway env."""
    raw = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    prev = os.environ.get(CA.APPROVAL_ENV)
    try:
        os.environ[CA.APPROVAL_ENV] = raw
        st = CA.approval_gate().state() or {}
    finally:
        if prev is None:
            os.environ.pop(CA.APPROVAL_ENV, None)
        else:
            os.environ[CA.APPROVAL_ENV] = prev
    return st, raw


def reserve_holds() -> tuple:
    """Does COMPLETE stay able to close every OPEN this budget permits?

    Walks the worst case: every OPEN consumes its full obligation of 2
    COMPLETE submissions. Reports the tightest margin reached.
    """
    o, c = CLASS_BUDGETS["OPEN"], CLASS_BUDGETS["COMPLETE"]
    per = AUTH.COMPLETION_SUBMISSIONS_PER_OBLIGATION
    worst = min(c - per * k - per for k in range(o))
    total_ok = (sum(CLASS_BUDGETS.values()) <= MAX_SUBMISSIONS)
    return worst, worst >= 0, total_ok, per


if __name__ == "__main__":
    doc = mint()
    st, raw = validate(doc)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    cid = "camp1." + hashlib.sha256(
        doc["nonce"].encode()).hexdigest()[:24]

    worst, ok, total_ok, per = reserve_holds()
    print("=" * 72)
    print("ISSUE 194 - TWO-HOUR BOUNDED CAMPAIGN - mint %s"
          % doc["issued_utc"])
    print("=" * 72)
    print("\n--- gate verdict (read-only; nothing reserved) ---")
    for k in ("approved", "reason", "state", "max_windows",
              "max_total_exposure_usd", "max_daily_loss_usd",
              "effective_expires_utc", "series"):
        if k in st:
            print("  %-26s %s" % (k, st[k]))
    if not st.get("approved"):
        print("\n  REFUSED: %s" % st.get("reason"))
        raise SystemExit(1)

    print("\n--- ceilings: asked vs code constant ---")
    for label, asked, ceil in (
            ("max_windows", WINDOWS, CA.MAX_WINDOWS),
            ("max_total_exposure_usd", EXPOSURE_USD,
             CA.MAX_TOTAL_EXPOSURE_USD),
            ("max_daily_loss_usd", DAILY_LOSS_USD, CA.MAX_DAILY_LOSS_USD),
            ("duration_s", DURATION_S, int(CA.MAX_CAMPAIGN_S))):
        print("  %-26s asked %-8s ceiling %-8s %s"
              % (label, asked, ceil,
                 "OK" if float(str(asked)) <= float(str(ceil)) else "EXCEEDS"))

    print("\n--- completion reserve (per obligation = %d) ---" % per)
    print("  class budgets            %s (sum %d / %d total)"
          % (CLASS_BUDGETS, sum(CLASS_BUDGETS.values()), MAX_SUBMISSIONS))
    print("  tightest COMPLETE margin %d  -> %s"
          % (worst, "reserve holds for every permitted OPEN" if ok
             else "RESERVE FAILS"))
    print("  class sum within total   %s" % total_ok)

    print("\n--- 1. install this campaign document ---")
    print("  %s=%s" % (CA.APPROVAL_ENV, raw))
    print("\n--- 2. and these budget variables ---")
    print("  L5_LIVE_AUTH_MAX_SUBMISSIONS=%d" % MAX_SUBMISSIONS)
    for k, v in CLASS_BUDGETS.items():
        print("  L5_LIVE_AUTH_MAX_%-9s=%d" % (k, v))
    print("  L5_MAX_TOTAL_EXPOSURE_USD=%s" % EXPOSURE_USD)
    print("  L5_MAX_CASH_DEPLOYED_USD=%s" % CASH_USD)
    print("  L5_MAX_L5_DAILY_LOSS_USD=%s" % DAILY_LOSS_USD)
    print("  L5_MAX_LIVE_ORDERS=2")
    print("  L5_LIVE_AUTH_MAX_WINDOWS=%d" % WINDOWS)

    print("\n--- audit identity ---")
    print("  campaign id     %s" % cid)
    print("  document_sha256 %s" % digest)
    print("  issued_utc      %s" % doc["issued_utc"])
    print("  expires_utc     %s" % doc["expires_utc"])
    print("  stale after     %s (issued + MAX_CAMPAIGN_S)"
          % (dt.datetime.fromisoformat(doc["issued_utc"])
             + dt.timedelta(seconds=CA.MAX_CAMPAIGN_S)).isoformat())
    print("\nHALT IS NOT CLEARED BY ANY OF THIS. Installing these variables")
    print("authorizes a campaign; it does not arm. L5_LIVE_HALT must still")
    print("be withdrawn deliberately, and that is a separate owner action.")
