"""LIVE-L5-EXEC-1-XR3 entrypoint — a long-lived DISARMED supervisor.

NOT DEPLOYED. Built and tested for review.

It boots, verifies every fingerprint in the lineage, connects AuthBook, and
then sits DISARMED, polling for an owner-signed authorization document. With
no valid document it never constructs an order transport. When one verifies,
it hands that single experiment to XR2's unchanged runner and returns to
idle.

Claude cannot author an authorization: this image holds only the owner's
public key, and that key is inside the fingerprint.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

from src import pmus_auth as AUTH
from analysis.l5.live_exec import spec as E
from analysis.l5.live_exec2 import spec as X1
from analysis.l5.live_exec2 import runner_spec as XR
from analysis.l5.live_exec3 import spec as XR2
from analysis.l5.live_exec4 import spec as X
from analysis.l5.live_exec4 import supervisor as SUP

FORBIDDEN_MODULES = ("src.l5_pmus_transport", "src.pmus_q1_executor",
                     "src.pmus_micro_executor")


def main() -> int:
    print("LIVE-L5-EXEC-1-XR3 SUPERVISOR — DISARMED", flush=True)
    frozen = True
    try:
        X.assert_frozen()
    except SystemExit as exc:
        frozen = False
        print("not frozen: %s" % exc, flush=True)
    forbidden = []
    for m in FORBIDDEN_MODULES:
        try:
            __import__(m)
            forbidden.append(m)
        except BaseException:                                  # noqa: BLE001
            pass
    pre: Dict[str, Any] = {
        "probe": X.PROBE, "role": "SUPERVISOR",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fingerprints": {"xr3": X.fingerprint(),
                         "xr3_authorized": X.XR3_FINGERPRINT,
                         "xr3_frozen": frozen,
                         "xr2": XR2.fingerprint(), "xr": XR.fingerprint(),
                         "exec1x": X1.fingerprint(), "exec1": E.fingerprint()},
        "entrypoint": X.SPEC["entrypoint"],
        "supervisor": X.SPEC["supervisor"],
        "two_ledgers": X.SPEC["two_ledgers"],
        "diff_from_xr2": X.diff_report(),
        "owner_public_key_supplied": X.owner_key_supplied(),
        "authorization_url_set": bool(os.getenv(X.AUTHZ_URL_ENV, "").strip()),
        "credentials_present_only": AUTH.has_credentials(),
        "legacy_executors_importable": forbidden,
        "claude_signed_anything": False,
        "railway": {k: os.getenv(k) for k in
                    ("RAILWAY_SERVICE_NAME", "RAILWAY_DEPLOYMENT_ID",
                     "RAILWAY_GIT_COMMIT_SHA")},
    }
    print("EXEC1XR3_PRE " + json.dumps(pre, default=str), flush=True)
    if not frozen:
        print("REFUSING TO RUN: the spec is not frozen.", flush=True)
        return 1
    if not X.owner_key_supplied():
        print("STAYING DISARMED FOREVER: this build carries the placeholder "
              "owner key, so no signature can verify. Supply the real "
              "public key, which changes the fingerprint, and re-authorize.",
              flush=True)

    book = None
    try:
        from src import l5_pmus_auth_book as AB
        book = AB.AuthBook().start()
    except BaseException as exc:                               # noqa: BLE001
        print("AuthBook did not start (%s); the supervisor stays DISARMED "
              "and each experiment's own book gate still applies"
              % type(exc).__name__, flush=True)

    sup = SUP.Supervisor(book=book)
    ticks = os.getenv("PMUS_EXEC1XR3_MAX_TICKS", "").strip()
    log: List[Dict[str, Any]] = sup.serve_forever(
        max_ticks=int(ticks) if ticks else None)
    print("EXEC1XR3_DONE " + json.dumps(
        {"ticks": len(log),
         "experiments_run": sup.experiments_run,
         "orders_submitted": sum(t.get("orders_submitted", 0) for t in log)},
        default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
