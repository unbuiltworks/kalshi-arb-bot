"""Emit the four-window PMUS live campaign approval document.

PREPARATION ONLY. This PRINTS a document; it authorizes nothing and touches
no venue, no variable and no service. The owner pastes the output into the
Railway variable L5_CAMPAIGN_APPROVAL on l5-pmus-live when they decide to
start the campaign -- the executor is forbidden from writing its own
approval (`EnvApprovalStore.put` raises deliberately).

THE FIGURES ARE THE OWNER-APPROVED ONES, 2026-10-26:
  max_windows              4
  max_total_exposure_usd   5     (code ceiling 20, so this TIGHTENS)
  max_daily_loss_usd       5     (equals the code ceiling)
  series                   cpc-btc-updown-15m-   (the only allowed series)

STALENESS, CORRECTED. The canonical gate is `approval_gate()`, which uses
max_age_s = MAX_CAMPAIGN_S = 4h -- NOT the 1-hour DEFAULT_MAX_AGE_S. That
one-hour clock exists for a PULL endpoint, which keeps serving yesterday's
approval unless age stops it; an owner-set variable is not that. So this
document is good for FOUR hours from issue, and `state()` clamps the
effective expiry to issued + 4h regardless of what expires_utc says.

A FRESH NONCE RESETS THE WINDOW COUNT. Consumption is durable and keyed on
nonce_sid = "camp1.<nonce>", so a new nonce starts its own 0/4 count. The
spent camp1.1ae75816714cfe65f0f256ad is left untouched as the audit record.
"""
from __future__ import annotations

import datetime as dt
import json

#: minted once, 24 hex chars (the gate refuses a nonce under 8)
NONCE = "87265e609a92b5a2889e5392"

SERIES = "cpc-btc-updown-15m-"
MAX_WINDOWS = 4
MAX_TOTAL_EXPOSURE_USD = "5"
MAX_DAILY_LOSS_USD = "5"
#: the campaign's own duration. The code clamps the effective expiry to
#: issued + 4h, so asking for more would be silently reduced, not honoured.
DURATION_S = 3 * 3600


def document(now: dt.datetime | None = None) -> dict:
    t = now or dt.datetime.now(dt.timezone.utc)
    return {
        "approved": True,
        "nonce": NONCE,
        "series": SERIES,
        "issued_utc": t.isoformat(),
        "expires_utc": (t + dt.timedelta(seconds=DURATION_S)).isoformat(),
        "max_windows": MAX_WINDOWS,
        "max_total_exposure_usd": MAX_TOTAL_EXPOSURE_USD,
        "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
    }


def main() -> int:
    print(json.dumps(document(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
