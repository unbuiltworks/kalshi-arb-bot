"""Emit the R=5 FREQUENCY experiment approval -- eight windows.

PREPARATION ONLY. This PRINTS a document. It authorizes nothing and touches
no venue, no variable and no service. The owner pastes the output into
L5_CAMPAIGN_APPROVAL on l5-pmus-live when they decide to start; the executor
is forbidden from writing its own approval (`EnvApprovalStore.put` raises
deliberately).

WHAT THIS EXPERIMENT TESTS. Whether additional completed pairs per window
raise net profit while unmatched inventory stays bounded. Measured per
window: completed rounds, total realized net P&L, and residual exposure.
The projected +$0.11/window is a PREDICTION TO TEST, not an expectation to
assume, and is not used anywhere in this document.

THE ENVELOPE, all at or inside existing code ceilings:
  max_windows              8    = CA.MAX_WINDOWS (the code ceiling)
  max_total_exposure_usd   20   = CA.MAX_TOTAL_EXPOSURE_USD, NOT raised
  max_daily_loss_usd       5    = CA.MAX_DAILY_LOSS_USD
  series                   cpc-btc-updown-15m-   (the only allowed series)

Carried by variables rather than by this document, which has no such fields:
  rounds per window        5    L5_TS_ROUND_ALLOCATIONS
  max cash deployed        $15  L5_MAX_CASH_DEPLOYED_USD
  opening clip             3    L5_TS_OPENING_LEG_MAX_CONTRACTS, unchanged
  pair-cost ceiling        0.96 TS.max_pair_cost(), unchanged
  first-leg floor          0.20 L5_TS_MIN_FIRST_LEG_PRICE, unchanged
  cumulative loss          $20  L5_MAX_CUMULATIVE_LOSS_USD, unchanged
  campaign-additional loss $5   L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD, unchanged

WHY 3 HOURS. Eight consecutive 15-minute windows span 2 hours; 3 hours
covers the run with margin for a window the engine skips. `state()` clamps
the effective expiry to issued + MAX_CAMPAIGN_S = 4h regardless.

A FRESH NONCE STARTS ITS OWN 0/8 COUNT. Consumption is durable and keyed on
nonce_sid = "camp1.<nonce>". The exhausted camp1.eb79a4b271122db56669c089
(8/8, realized -$1.4140) is left untouched as the audit record and is
neither reset nor extended.

THIS DOCUMENT DOES NOT ARM ANYTHING. Arming is a separate handshake
(PMUS_Q1_LIVE / PMUS_Q1_ARM / PMUS_Q1_SESSION_ID) and L5_LIVE_AUTH_NOT_AFTER
must also be refreshed. A campaign BOUNDS a run; it does not authorize one.
"""
from __future__ import annotations

import datetime as dt
import json

#: minted for this experiment, 24 hex chars (the gate refuses a nonce under 8)
NONCE = "4f1b25fe655d20d53ca8d698"

SERIES = "cpc-btc-updown-15m-"
MAX_WINDOWS = 8
MAX_TOTAL_EXPOSURE_USD = "20"
MAX_DAILY_LOSS_USD = "5"
DURATION_S = 3 * 3600


def document(now: dt.datetime | None = None) -> dict:
    t = now or dt.datetime.now(dt.timezone.utc)
    return {
        "approved": True,
        "nonce": NONCE,
        "series": SERIES,
        "issued_utc": t.replace(microsecond=0).isoformat(),
        "expires_utc": (t + dt.timedelta(seconds=DURATION_S)
                        ).replace(microsecond=0).isoformat(),
        "max_windows": MAX_WINDOWS,
        "max_total_exposure_usd": MAX_TOTAL_EXPOSURE_USD,
        "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
    }


def main() -> int:
    import hashlib
    d = document()
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
    print(blob)
    print()
    print("# sha256      :", hashlib.sha256(blob.encode()).hexdigest())
    print("# nonce_sid   : camp1.%s" % NONCE)
    print("# windows     : 0 of %d consumed" % MAX_WINDOWS)
    print("# effective   : issued + min(%ss, 4h)" % DURATION_S)
    print("# AUTHORIZES  : nothing. Paste into L5_CAMPAIGN_APPROVAL to BOUND")
    print("#               a run; arming is a separate handshake.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
