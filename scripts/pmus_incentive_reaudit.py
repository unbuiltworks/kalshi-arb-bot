#!/usr/bin/env python3
"""READ-ONLY credentialed re-audit of PMUS incentive credits, as its OWN
measurement window.

WHY THIS SCRIPT EXISTS. `src.pmus_incentive_audit.incentive_audit` is
read-only, but the only thing that called it was
`src/pmus_l5_continuous_session.py` -- a money-moving session. Measuring three
weeks of possible credits must not require booting an execution session, so
this is a standalone entrypoint that issues GETs and nothing else.

WHAT IT DOES NOT DO. It never places, cancels, previews, signs or replaces an
order: it holds no code path that could, and it refuses to run if the
execution arming variables are set (see `_refuse_if_armed`). It never prints,
echoes or logs a credential VALUE -- credential state is reported as a
presence boolean only.

THE GAP IS A HOLE, NOT A ZERO. The committed incentive figures ($9.14 maker
rebates / $7.04 liquidity rewards / $16.18) are measured to
2026-09-14T22:59:21Z. Everything after that is UNMEASURED. The output is
therefore named for its own retrieval instant and carries
`measurement_window.prior_measurement_ended_utc`, so the new reading can never
be silently merged into the 2026-08-14..09-14 window.

USAGE (on the host that holds the PMUS credential, which must NOT be the
service carrying an armed probe):

    python3 -m scripts.pmus_incentive_reaudit \
        --out analysis/l5/incentives/PMUS_INCENTIVES_REAUDIT_<ISO>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import pmus_fee_schedule as fee_schedule          # noqa: E402
from src import pmus_incentive_audit as ia                 # noqa: E402
from src import pmus_q1_executor as q1                     # noqa: E402

#: the end of the measurement this run EXTENDS; never merged into it
PRIOR_MEASUREMENT_ENDED_UTC = "2026-09-14T22:59:21.565847915Z"
PRIOR_MEASUREMENT = {
    "window_utc": ["2026-08-14T03:26:06Z", PRIOR_MEASUREMENT_ENDED_UTC],
    "maker_rebates_credited_usd": "9.14",
    "maker_rebate_credited_fills": "737 of 1174",
    "liquidity_rewards_paid_usd": "7.04",
    "taker_rebates_usd": "0.00",
    "volume_program_usd": "0.00",
    "confirmed_incentives_usd": "16.18",
    "source": "analysis/l5/incentives/TRANSFER_2PT51.md (Ledger C)",
}

#: arming variables for the EXECUTION path. If any is set, this host may be
#: the one carrying a live probe, and a read-only audit has no business
#: sharing that process.
ARMING_ENVS = (q1.LIVE_ENV, q1.ARM_ENV, q1.SID_ENV, q1.GATE_ENV)


def credential_presence() -> dict:
    """Presence BOOLEANS ONLY. No value is read into the output, logged, or
    included in an error message."""
    return {name: bool(os.getenv(name, "").strip())
            for name in (q1.KEY_ENV, q1.SECRET_ENV)}


def _refuse_if_armed() -> None:
    set_now = [n for n in ARMING_ENVS if os.getenv(n, "").strip()]
    if set_now:
        raise SystemExit(
            "REFUSING: execution arming variables are set in this process "
            f"({', '.join(set_now)}). Run the read-only re-audit on a service "
            "that is NOT carrying an armed probe.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True,
                    help="artifact path; name it for THIS retrieval instant")
    ap.add_argument("--dry-run", action="store_true",
                    help="check safety preconditions and exit without any GET")
    args = ap.parse_args(argv)

    _refuse_if_armed()
    fee_schedule.assert_frozen()
    fee_schedule.assert_consistent_with_pmus_fees()

    started = dt.datetime.now(dt.timezone.utc).isoformat()
    presence = credential_presence()
    header = {
        "kind": "incentive_reaudit",
        "retrieved_at_utc": started,
        "credential_presence": presence,
        "fee_schedule_fingerprint": fee_schedule.FINGERPRINT,
        "measurement_window": {
            "this_reading_is_its_own_measurement": True,
            "prior_measurement_ended_utc": PRIOR_MEASUREMENT_ENDED_UTC,
            "prior_measurement": PRIOR_MEASUREMENT,
            "merge_rule": ("the unmeasured interval is a HOLE, not a zero; "
                           "report this reading beside the prior window, "
                           "never summed into it without a stated window"),
        },
        "labels": ("OBSERVED / venue-credited only is realized; formula "
                   "figures are ESTIMATED and never counted"),
        "read_only": "every transport call on this path is a GET",
    }
    if not all(presence.values()):
        header.update({"ok": False,
                       "error": "PMUS credential not present in this process "
                                "(presence checked by name only)"})
        Path(args.out).write_text(json.dumps(header, indent=1, default=str))
        print(json.dumps({k: header[k] for k in
                          ("kind", "retrieved_at_utc", "credential_presence",
                           "ok", "error")}, indent=1))
        return 2
    if args.dry_run:
        header.update({"ok": True, "dry_run": True})
        print(json.dumps(header, indent=1, default=str))
        return 0

    out = dict(header)
    out["audit"] = ia.incentive_audit(q1.Transport())
    out["ok"] = True
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    v = (out["audit"] or {}).get("verdict") or {}
    print(json.dumps({"wrote": args.out, "retrieved_at_utc": started,
                      "verdict": v}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
