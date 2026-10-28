#!/usr/bin/env python3
"""Generate ONE bounded-campaign approval document.

WHY THIS IS A SCRIPT AND NOT A FEATURE OF THE EXECUTOR. The executor must
never be able to write its own authorization -- `EnvApprovalStore.put`
refuses for exactly that reason. The document is produced OUTSIDE the
service and placed in the owner-controlled variable
`L5_CAMPAIGN_APPROVAL`, which is the one owner action the whole layer
exists to reduce the count of: one per CAMPAIGN, not one per window.

WHAT THE DOCUMENT CANNOT DO. It cannot widen anything. Every ceiling is a
code constant in `src/l5_campaign_approval.py` and the effective scope is
the INTERSECTION of what this asks for and what the code permits. Asking
for more yields less, silently and by design, and the gate reports every
field it clamped.

THE NONCE IS WRITTEN TO THE FILE, NEVER TO STDOUT. stdout carries the
sha256 digest and the non-secret fields, so a terminal, a CI log or a chat
transcript can confirm WHICH document is in force without carrying it.

    python3 tools/make_campaign_approval.py /secure/path/approval.json
    # then paste the file's contents into L5_CAMPAIGN_APPROVAL

Rotating: run it again. A new nonce gets its own window budget; the old
nonce's spent windows stay spent, so a rotation cannot recycle them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from src import l5_campaign_approval as CA        # noqa: E402

#: what we ASK for. The code's own ceilings decide what is granted.
#
# OVERRIDABLE PER CAMPAIGN, because the ask is a per-campaign decision and
# editing this file to change one was the previous ceremony. The defaults
# are exactly the values that were hardcoded here, so a run with no
# overrides is byte-for-byte what it always produced. Asking for more than
# the code constants permits still yields less, silently and by design --
# the gate reports every field it clamped, and this script verifies the
# document against that real gate before calling it valid.
ASK_WINDOWS = int(os.getenv("ASK_WINDOWS", "4"))
ASK_EXPOSURE_USD = os.getenv("ASK_EXPOSURE_USD", "5")
ASK_DAILY_LOSS_USD = os.getenv("ASK_DAILY_LOSS_USD", "5")
ASK_DURATION_S = int(os.getenv("ASK_DURATION_S", str(4 * 3600)))
#: THE SUBMISSION BUDGETS, now GRADED and no longer audit-trail-only. The
#: runtime-config verifier reads them from the document and compares them
#: with the deployed variables, so a value left over from a previous
#: campaign fails instead of passing quietly. The defaults are the figures
#: this script hardcoded before, so a run with no overrides is unchanged.
ASK_OPEN = int(os.getenv("ASK_OPEN", "8"))
ASK_COMPLETE = int(os.getenv("ASK_COMPLETE", "12"))
ASK_REPLACE = int(os.getenv("ASK_REPLACE", "16"))
ASK_TOTAL = int(os.getenv("ASK_TOTAL", "28"))


def build(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    return {
        "approved": True,
        # 32 bytes of os entropy. Not a credential -- writing the variable
        # is the authority -- but single-use, so it must not collide.
        "nonce": "camp-%s" % secrets.token_urlsafe(24),
        "series": CA.ALLOWED_SERIES[0],
        "issued_utc": now.isoformat(),
        "expires_utc": (now + dt.timedelta(seconds=ASK_DURATION_S)
                        ).isoformat(),
        "max_windows": ASK_WINDOWS,
        "max_total_exposure_usd": ASK_EXPOSURE_USD,
        "max_daily_loss_usd": ASK_DAILY_LOSS_USD,
        # carried for the audit trail. The ARMING gate still ignores every
        # field below -- the limits that bind are the code constants and
        # the env budgets -- but the four submission budgets are now read
        # by the runtime-config verifier and graded against the deployed
        # variables, so they are no longer decorative.
        "strategy": "TWO_SIDED",
        "pair_cost_ceiling": "0.96",
        "max_live_orders": 1,
        # THE THREE DURABLE CLASS BUDGETS, campaign-wide. One flat total
        # cannot tell an order that takes on risk from one that unwinds it,
        # so it is spent by the first kind and refuses the second.
        "max_open_submissions": ASK_OPEN,
        "max_complete_submissions": ASK_COMPLETE,
        "max_replace_submissions": ASK_REPLACE,
        # the finite backstop, deliberately BELOW the sum of the three, so
        # no two classes can both run to their ceiling. It does NOT bind a
        # risk-reducing order: a global count that can refuse an order
        # which provably reduces |unmatched| can only STRAND risk.
        # NAMED TO MATCH THE VERIFIER, which reads `max_total_submissions`.
        # This field was `max_submissions_total` while nothing read it; the
        # two spellings would have left the backstop ungraded, which is the
        # one budget whose drift silenced a whole window.
        "max_total_submissions": ASK_TOTAL,
        "completion_reserve_submissions": 2,
        "backstop_exempts_risk_reducing": True,
        "cancellations": "UNBUDGETED: a bound that could refuse a cancel is "
                         "a bound that traps an order on the venue",
    }


def main(argv: list) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: make_campaign_approval.py <out.json>\n")
        return 2
    out = argv[1]
    doc = build()
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())

    # VERIFY IT AGAINST THE REAL GATE before telling anyone it is valid.
    # A document that only this script approves is worth nothing.
    class _S:
        def get(self, key):
            return body.encode() if key == CA.APPROVAL_KEY else None

    st = CA.ApprovalGate(_S()).state()
    safe = {k: v for k, v in doc.items() if k != "nonce"}
    safe["nonce"] = "<%d chars, written to %s only>" % (len(doc["nonce"]), out)
    print(json.dumps({
        "wrote": out,
        "document_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "gate_says_approved": bool(st.get("approved")),
        "gate_why": st.get("why"),
        "effective_scope": {k: v for k, v
                            in (st.get("scope") or {}).items()
                            if k not in ("nonce", "nonce_sid")},
        "asked": safe,
        "next": ("paste the file's contents into L5_CAMPAIGN_APPROVAL; the "
                 "nonce never needs to leave that variable"),
    }, indent=2, sort_keys=True))
    return 0 if st.get("approved") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
