"""Emit the EIGHT-window PMUS L5 sequential-pairing experiment approval.

PREPARATION ONLY. This PRINTS a document. It authorizes nothing and touches
no venue, no variable and no service. The owner pastes the output into the
Railway variable L5_CAMPAIGN_APPROVAL on l5-pmus-live when they decide to
start -- the executor is forbidden from writing its own approval
(`EnvApprovalStore.put` raises deliberately).

OWNER-APPROVED ENVELOPE, 2026-10-26:
  max_windows              8    (equals the code ceiling CA.MAX_WINDOWS)
  max_total_exposure_usd   20   (equals the code ceiling; NOT raised)
  max_daily_loss_usd       5    (equals the code ceiling)
  cumulative loss          10   (carried by L5_MAX_CUMULATIVE_LOSS_USD, not
                                 by this document, which has no such field)
  series                   cpc-btc-updown-15m-   (the only allowed series)

WHY 3 HOURS. Eight consecutive 15-minute windows span 2 hours, so 3 hours
covers the run with margin for a window the engine skips. `state()` clamps
the effective expiry to issued + MAX_CAMPAIGN_S = 4h regardless, so asking
for more would be silently reduced rather than honoured.

STALENESS. The canonical gate is `approval_gate()`, which uses
max_age_s = MAX_CAMPAIGN_S = 4h, not the 1-hour DEFAULT_MAX_AGE_S (that
clock guards a PULL endpoint, which would otherwise keep serving
yesterday's approval; an owner-set variable is not that).

A FRESH NONCE RESETS THE WINDOW COUNT. Consumption is durable and keyed on
nonce_sid = "camp1.<nonce>", so this nonce starts its own 0/8 count. The
spent camp1.87265e609a92b5a2889e5392 (4/4, expired 17:42:45Z) is left
untouched as the audit record.

THIS DOCUMENT DOES NOT ARM ANYTHING. Arming is a separate handshake
(PMUS_Q1_LIVE / PMUS_Q1_ARM / PMUS_Q1_SESSION_ID) and the absolute deadline
L5_LIVE_AUTH_NOT_AFTER must also be refreshed. A campaign BOUNDS a run; it
does not authorize one.
"""
from __future__ import annotations

import datetime as dt
import json

#: minted for this experiment, 24 hex chars (the gate refuses a nonce under 8)
NONCE = "eb79a4b271122db56669c089"

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
