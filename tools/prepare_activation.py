#!/usr/bin/env python3
"""Produce the ONE atomic activation patch for the bounded campaign.

TWO PATCHES, AND THE ORDER IS THE SAFETY PROPERTY.

  PATCH A installs the SCOPE while both trading locks are still on: the
          approval document, the BTC15m series mandate, the four-window
          budget, the absolute deadline, TWO_SIDED exclusivity, the three
          class budgets and the backstop. It CLEARS the stale exact-market
          pin, which would otherwise refuse every window but the one it
          names. Nothing can submit while this lands.
  PATCH B lifts the locks, and only after Patch A has been VERIFIED in a
          live heartbeat.

There is therefore never an interval in which live submission is enabled
without a valid campaign and a market scope -- which is the failure mode
this ordering exists to prevent. Doing it the other way round, even for one
redeploy, would leave the executor live with the series mandate unset.

THE NONCE NEVER REACHES STDOUT. Patch A's variable map is written to a 0600
file; stdout carries the document's sha256 and the non-secret fields.

    python3 tools/prepare_activation.py <outdir>
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from src import l5_campaign_approval as CA          # noqa: E402
from src import l5_live_authorization as AUTH       # noqa: E402
from tools.make_campaign_approval import build      # noqa: E402

#: the approved budgets. Code constants here too, so a typo at the keyboard
#: cannot widen what was approved.
BUDGETS = {
    AUTH.CLASS_BUDGET_ENV["OPEN"]: "8",
    AUTH.CLASS_BUDGET_ENV["COMPLETE"]: "12",
    AUTH.CLASS_BUDGET_ENV["REPLACE"]: "16",
    AUTH.MAX_SUBMISSIONS_ENV: "28",
}


def main(argv: list) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: prepare_activation.py <outdir>\n")
        return 2
    out = argv[1]
    os.makedirs(out, mode=0o700, exist_ok=True)

    doc = build()
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode()).hexdigest()

    # VERIFY IT AGAINST THE REAL GATE before anyone installs it.
    class _S:
        def get(self, key):
            return body.encode() if key == CA.APPROVAL_KEY else None

    gate = CA.ApprovalGate(_S(), max_age_s=CA.MAX_CAMPAIGN_S)
    st = gate.state()
    if not st.get("approved"):
        sys.stderr.write("REFUSING: the gate does not approve this "
                         "document: %s\n" % st.get("why"))
        return 1
    scope = dict(st.get("scope") or {})

    # the deadline the MANDATE requires, set to the campaign's own ceiling so
    # the two cannot disagree. A mandate above one window with no absolute
    # deadline is refused by authorize_arming, and a deadline SHORTER than
    # the campaign would silently cut it off mid-run.
    not_after = scope["expires_utc"]

    patch_a = dict(BUDGETS)
    patch_a[CA.APPROVAL_ENV] = body
    # A FRESH EXPERIMENT ID, SO THE CAMPAIGN'S SUBMISSION BUDGETS START AT
    # ZERO. The three class budgets and the flat backstop are counted from
    # durable lock files keyed on this id, so a new id gives the campaign its
    # own budget and leaves the previous campaign's claims on the volume,
    # named after the id that spent them.
    #
    # WHAT IT DOES NOT RESET, and this is the point: the InventoryStore is
    # keyed on `<L5_LIVE_ROOT>/inventory.json`, not on the experiment, so
    # positions, realized P&L, retired_markets, account-wide exposure and the
    # cumulative loss gates are untouched. The halt latch belongs to the
    # watchdog. The daily halt reads the store's own UTC-day settlement rows.
    # Not one safety counter is keyed on this id.
    #
    # READABLE, NOT SECRET: it names the claim files an auditor has to find.
    patch_a[AUTH.EXPERIMENT_ENV] = "l5-campaign-%s" % dt.datetime.now(
        dt.timezone.utc).strftime("%Y%m%dT%H%Mz")
    patch_a["L5_WINDOW_STRATEGY"] = "TWO_SIDED"
    patch_a[AUTH.SERIES_ENV] = CA.ALLOWED_SERIES[0]
    patch_a[AUTH.MAX_WINDOWS_ENV] = str(scope["max_windows"])
    patch_a[AUTH.NOT_AFTER_ENV] = not_after
    # THE STALE PIN. Empty, not absent: `authorize_arming` reads
    # `os.getenv(MARKET_ENV, "").strip()` and an empty value is "no exact
    # market", which hands scope to the series mandate above rather than
    # removing it.
    patch_a[AUTH.MARKET_ENV] = ""

    patch_b = {"L5_SHADOW_ONLY": "0", "PMUS_Q1_LIVE": "YES"}

    pa = os.path.join(out, "patch_a.json")
    fd = os.open(pa, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(patch_a, fh, indent=1, sort_keys=True)
    pb = os.path.join(out, "patch_b.json")
    with open(pb, "w") as fh:
        json.dump(patch_b, fh, indent=1, sort_keys=True)

    print(json.dumps({
        "document_sha256": digest,
        "gate_says_approved": True,
        "effective_scope": {k: v for k, v in scope.items()
                            if k not in ("nonce", "nonce_sid")},
        "patch_a_file": pa,
        "patch_a_keys": sorted(patch_a),
        "patch_a_clears_the_stale_pin": AUTH.MARKET_ENV,
        "patch_a_non_secret_values": {
            k: v for k, v in sorted(patch_a.items())
            if k != CA.APPROVAL_ENV},
        "patch_b_file": pb,
        "patch_b": patch_b,
        "order": ("A installs the scope with both locks ON; verify the "
                  "heartbeat; then B lifts the locks. Never the reverse: "
                  "that would leave live submission enabled with the series "
                  "mandate unset."),
        "verify_between_a_and_b": [
            "campaign.approval_state == approved",
            "campaign.document_sha256 == %s" % digest,
            "campaign.windows_remaining == %s" % scope["max_windows"],
            "campaign.blocks_advancement == false",
            "campaign.exact_market_binding == null",
            "campaign.class_budgets == OPEN 8 / COMPLETE 12 / REPLACE 16",
            "campaign.completion_reserve.may_open == true",
            "campaign.class_budgets all spent == 0 under the new "
            "experiment id",
            "campaign.submissions_spent == 0",
            "day_realized.would_halt == false and room_usd > 0",
            "submission_gate.halt_latch == CLEAR and kind == PRESENT",
            "unresolved_accounting.count == 0",
            "L5_BUDGET_SELFCHECK.all_passed == true "
            "and missing_checks == []",
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
