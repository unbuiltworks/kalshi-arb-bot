#!/usr/bin/env python3
"""Emit the UNSIGNED seven-day mandate payload, and prove it would verify.

THIS TOOL CANNOT AUTHORIZE ANYTHING. It writes a payload with no signature.
A payload is inert: `verify` refuses `MANDATE_SIGNATURE_FIELDS_MISSING`
until the owner signs it offline with `tools/owner_sign_mandate.py`, using a
private key that does not exist in this repository, in Railway, or in any
environment the executor can read.

WHY IT EXISTS. Every field below is required, no extra field is allowed, a
JSON float where a money string belongs is a refusal, and a timestamp
without an offset is a refusal. Discovering that by signing a payload and
watching production refuse it is an expensive way to find a typo. So this
builds the document from the approved envelope and runs the REAL verifier's
own field checks over it before anyone signs.

    python3 tools/make_mandate_payload.py                 # print + validate
    python3 tools/make_mandate_payload.py out.json        # also write it

`--start` defaults to the next UTC midnight so the window is a whole number
of days and does not begin mid-session.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

# the repo root, so `from src import ...` works however this is invoked
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_mandate_signature as MS        # noqa: E402

#: THE APPROVED ENVELOPE, unchanged from campaigns 8 and 9. Every number
#: here is one the owner has already approved for live trading; this
#: document changes the DURATION and the shape of the budget (per-day rather
#: than per-campaign), never a limit.
DAYS = 7
MANDATE_ID = "l5-btc15m-standing-001"
SEQ = 1

#: 8 windows and 72 submissions were the per-CAMPAIGN budget. As a per-DAY
#: rate they are the same ceiling applied to a day instead of to a 4-hour
#: campaign, which is a REDUCTION in rate for any day that would otherwise
#: have run two campaigns.
MAX_WINDOWS_PER_DAY = 8
MAX_SUBMISSIONS_PER_DAY = 72


def build(start: dt.datetime) -> dict:
    return {
        "schema": MS.SCHEMA,
        "kind": "standing",
        "mandate_id": MANDATE_ID,
        "seq": SEQ,
        "venue": "PMUS",
        "strategy": "l5-two-sided-btc15m",
        "environment": "production",
        "service_role": "l5-pmus-live",
        "series": "cpc-btc-updown-15m-",
        "not_before": start.isoformat(),
        "not_after": (start + dt.timedelta(days=DAYS)).isoformat(),
        "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
        "execution_mode": "MAKER_ONLY",
        "max_windows_per_day": MAX_WINDOWS_PER_DAY,
        "max_submissions_per_day": MAX_SUBMISSIONS_PER_DAY,
        "capital": {
            "max_cash_deployed_usd": "12",
            "max_total_exposure_usd": "20",
            "campaign_budget_usd": "29.30",
        },
        "loss_limits": {
            "daily_realized_loss_halt_usd": "5",
            "max_cumulative_loss_usd": "20",
        },
        "residual_cap_contracts": 3,
        "promotion": {
            "min_settled_windows": 300,
            "min_expectancy_usd": "0.05",
            "oos_evidence_id": "btc15m-oos-20261023",
            "requires_bounded_live_validation": True,
        },
    }


def validate(payload: dict) -> list:
    """Run the REAL verifier's field checks. Returns a list of reasons.

    VALIDITY IS CHECKED FROM INSIDE THE WINDOW, not from now. A mandate that
    starts at the next midnight is correctly `MANDATE_NOT_YET_VALID` today,
    and reporting that as a defect would train the reader to ignore this
    output. The question here is "would this verify once it is live", so the
    clock is set an hour after `not_before`. Expiry, the duration ceiling
    and an inverted window are all still caught, because they are properties
    of the two timestamps rather than of the current time.
    """
    out = []
    try:
        nb = dt.datetime.fromisoformat(
            str(payload.get("not_before")).replace("Z", "+00:00"))
        now = nb + dt.timedelta(hours=1)
    except (ValueError, TypeError):
        now = dt.datetime.now(dt.timezone.utc)
    for check, args in (
            (MS._check_schema, (payload,)),
            (MS._check_binding, (payload,)),
            (MS._check_numbers, (payload,)),
            (MS._check_validity, (payload, now)),
    ):
        try:
            if check is MS._check_binding:
                bad = check(payload, environment="production",
                            service_role="l5-pmus-live",
                            series="cpc-btc-updown-15m-")
            else:
                bad = check(*args)
        except TypeError as exc:          # signature drift -> say so loudly
            out.append("CHECK_SIGNATURE_DRIFT:%s:%s" % (check.__name__, exc))
            continue
        if bad:
            out.append(str(bad.get("reason") or bad))
    return out


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", help="write the payload here")
    ap.add_argument("--start", default="",
                    help="ISO start with offset; default next UTC midnight")
    a = ap.parse_args(argv[1:])

    if a.start:
        start = dt.datetime.fromisoformat(a.start.replace("Z", "+00:00"))
        if start.tzinfo is None:
            sys.stderr.write("--start needs an explicit offset\n")
            return 2
    else:
        n = dt.datetime.now(dt.timezone.utc)
        start = (n + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    payload = build(start)
    problems = validate(payload)

    report = {
        "payload": payload,
        "days": DAYS,
        "validates_against_real_verifier": not problems,
        "problems": problems,
        "signed": False,
        "next": ("sign offline with tools/owner_sign_mandate.py; this "
                 "payload authorizes nothing until it carries the owner's "
                 "Ed25519 signature AND a trusted key is registered AND "
                 "L5_MANDATE_AS_AUTHORIZATION is set"),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if a.out and not problems:
        with open(a.out, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        sys.stderr.write("wrote %s\n" % a.out)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
