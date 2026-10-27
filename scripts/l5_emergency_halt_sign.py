"""Produce a signed emergency-HALT marker. OWNER TOOL, run locally.

Deliberately NOT in any image: it needs the HMAC secret, and nothing
deployed should be able to mint a marker. The watchdog only verifies.

    L5_EMERGENCY_HALT_SECRET=... python3 -m scripts.l5_emergency_halt_sign \
        --reason "stopping for X" > EMERGENCY_HALT.json

Then publish that file at L5_EMERGENCY_HALT_URL. The watchdog picks it up
within one poll interval and latches HALT; the executor's own
maybe_halt_deadman then cancels by exact venue id and re-reads to verify.

It prints the marker to stdout and NEVER prints the secret.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid

from src import l5_emergency_halt_marker as M


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reason", required=True,
                    help="why, recorded in the halt evidence")
    ap.add_argument("--service", default="l5-pmus-live",
                    help="which deployment this marker may halt")
    ap.add_argument("--nonce", default=None,
                    help="defaults to a fresh uuid4; reusing one is a no-op")
    a = ap.parse_args(argv)

    secret = (os.getenv(M.MARKER_SECRET_ENV) or "").strip()
    if not secret:
        print("%s is not set; refusing to emit an unsigned marker"
              % M.MARKER_SECRET_ENV, file=sys.stderr)
        return 2

    doc = {"command": M.COMMAND,
           "service": a.service,
           "issued_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "nonce": a.nonce or ("halt-" + uuid.uuid4().hex[:16]),
           "reason": a.reason}
    doc["hmac"] = M.sign(doc, secret)
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
