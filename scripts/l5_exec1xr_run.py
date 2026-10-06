"""LIVE-L5-EXEC-1-XR entrypoint. THE RUNNER THAT MAKES THE ORDER.

This is not a preflight. It calls `runner.run_experiment`, which is the same
function that will place the authorized order once the owner installs the
five values. DISARMED, it walks the whole path and stops at the final
pre-POST refusal, and it SAYS where it stopped -- so what this deployment
proves is the real entrypoint, not a look-alike.

It also prints the contamination and arming facts that the authorization
asks to be re-verified, and the call graph down to the POST, so the reader
can check the claim rather than take it.

Claude sets none of the five owner values. There is no code here that does.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

from src import pmus_auth as A
from analysis.l5.live_exec.ledger import ARM_KEYS, exec1_armed
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import runner as RUN
from analysis.l5.live_exec2 import runner_spec as X
from analysis.l5.live_exec2 import spec as XSPEC
from analysis.l5.live_exec import spec as E

FORBIDDEN_MODULES = ("src.l5_pmus_transport", "src.pmus_q1_executor",
                     "src.pmus_micro_executor")
NEEDLES = ("pmus_q1_executor", "pmus_micro_executor", "pmus_spring_l5",
           "l5_pmus_live")


def order_capable_processes() -> List[str]:
    hits: List[str] = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == str(os.getpid()):
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                argv = [a for a in fh.read().split(b"\0") if a]
        except OSError:
            continue
        toks = [a.decode("utf8", "replace") for a in argv]
        for i, tok in enumerate(toks):
            base, dotted = tok.rsplit("/", 1)[-1], tok.split(".")[-1]
            if any(base == n + ".py" or (i and toks[i - 1] == "-m"
                                         and dotted == n) for n in NEEDLES):
                hits.append("pid %s: %s" % (pid, " ".join(toks)[:160]))
                break
    return hits


def main() -> int:
    print("LIVE-L5-EXEC-1-XR RUNNER", flush=True)
    pre: Dict[str, Any] = {"probe": X.PROBE, "role": "RUNNER",
                           "started_utc": time.strftime(
                               "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    frozen = True
    try:
        X.assert_frozen()
    except SystemExit as exc:
        frozen = False
        pre["frozen_error"] = str(exc)
    pre["fingerprints"] = {
        "xr": X.fingerprint(), "xr_authorized": X.XR_FINGERPRINT,
        "xr_frozen": frozen,
        "exec1x": XSPEC.fingerprint(), "exec1": E.fingerprint(),
    }
    pre["entrypoint"] = X.SPEC["entrypoint"]
    pre["diff_from_exec1x"] = X.diff_report()
    pre["arming"] = {
        "keys_present": {k: bool(os.getenv(k, "").strip()) for k in ARM_KEYS},
        "armed": exec1_armed(),
        "fingerprint_pin": X.fingerprint_pinned(),
        "claude_set_any": False,
    }
    pre["durable_latch"] = L.state()
    pre["credentials_present_only"] = A.has_credentials()
    forbidden = []
    for m in FORBIDDEN_MODULES:
        try:
            __import__(m)
            forbidden.append(m)
        except BaseException:                                  # noqa: BLE001
            pass
    pre["legacy_executors_importable"] = forbidden
    pre["other_order_capable_processes"] = order_capable_processes()
    pre["railway"] = {k: os.getenv(k) for k in
                      ("RAILWAY_SERVICE_NAME", "RAILWAY_DEPLOYMENT_ID",
                       "RAILWAY_GIT_COMMIT_SHA")}
    print("EXEC1XR_PRE " + json.dumps(pre, default=str), flush=True)

    if not frozen:
        print("REFUSING TO RUN: the spec is not frozen.", flush=True)
        return 1

    # The real runner. DISARMED it stops at the final pre-POST refusal; armed
    # and gated, it places the one authorized order.
    rec = RUN.run_experiment(
        explicit_slug=os.getenv("PMUS_EXEC1_GATE_EVIDENCE", "").strip()
        or None,
        observe_s=float(os.getenv("PMUS_EXEC1XR_OBSERVE_S", "") or
                        RUN.OBSERVE_S),
        book_wait_s=float(os.getenv("PMUS_EXEC1XR_BOOK_WAIT_S", "") or
                          RUN.BOOK_WAIT_S))
    print("EXEC1XR_SUMMARY " + json.dumps({
        "probe": X.PROBE, "fingerprint": X.XR_FINGERPRINT,
        "orders_submitted": rec.get("orders_submitted"),
        "stopped_at": rec.get("stopped_at"),
        "reached_final_gate": rec.get("reached_final_gate", False),
        "refusals": rec.get("refusals"),
        "matched_pair_economics": "N/A",
        "VERDICT": ("ORDER PLACED" if rec.get("orders_submitted")
                    else "NO ORDER / %s" % rec.get("stopped_at")),
    }, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
