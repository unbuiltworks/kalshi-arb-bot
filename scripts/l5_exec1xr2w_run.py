"""LIVE-L5-EXEC-1-XR2W entrypoint. THE RUNNER THAT MAKES THE ORDER.

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
from analysis.l5.live_exec3 import runner as RUN
from analysis.l5.live_exec5 import spec as X
from analysis.l5.live_exec3 import spec as XR2PARENT
from analysis.l5.live_exec2 import runner_spec as XR
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
    print("LIVE-L5-EXEC-1-XR2W RUNNER", flush=True)
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
        "xr2w": X.fingerprint(), "xr2w_authorized": X.XR2W_FINGERPRINT,
        "xr2w_frozen": frozen,
        # Parents, printed for lineage ONLY. Each is SUPERSEDED: the wire
        # defect was in shared code, so none of them describes the code
        # that would now run, and none of them can arm this build.
        "SUPERSEDED_parents": {
            "xr2": XR2PARENT.fingerprint(),
            "xr": XR.fingerprint(),
            "exec1x": XSPEC.fingerprint(),
            "exec1": E.fingerprint(),
        },
        "superseded_must_not_be_rearmed":
            X.SPEC["supersedes"]["must_not_be_rearmed"],
    }
    pre["fingerprint_pin_policy"] = X.SPEC["fingerprint_pin"]
    pre["wire_serialization"] = X.SPEC["wire_serialization"]
    pre["lifecycle_classification"] = X.SPEC["lifecycle_classification"]
    pre["entrypoint"] = X.SPEC["entrypoint"]
    pre["diff_from_xr2"] = X.diff_report()
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
    # Prove the proven serializer is importable IN THIS IMAGE and produces
    # the frozen wire shape. A missing COPY would otherwise surface as an
    # ImportError at the decision point, seconds from the placement cutoff.
    try:
        from src import l5_order_body as _OB
        from analysis.l5.live_exec5 import fixture_a5 as _F
        _b = _OB.build(slug=_F.A5["slug"], side="DOWN", price="0.49",
                       qty="0.01", post_only=True)
        pre["serializer"] = {
            "module": "src.l5_order_body", "importable": True,
            "a5_fixture_body_matches": _b == _F.A5_CORRECTED_ORDER_BODY,
            "preview_envelope_matches":
                _OB.preview_envelope(_b) == _F.A5_CORRECTED_PREVIEW_REQUEST,
            "quantity_type": type(_b["quantity"]).__name__,
            "price": _b["price"],
            "semantic_down_0_49_to_wire": {
                "outcomeSide": _b["outcomeSide"], "action": _b["action"],
                "price_value": _b["price"]["value"]},
        }
    except BaseException as exc:                               # noqa: BLE001
        pre["serializer"] = {"module": "src.l5_order_body",
                             "importable": False,
                             "error": type(exc).__name__, "detail": str(exc)}
    print("EXEC1XR2W_PRE " + json.dumps(pre, default=str), flush=True)

    if not pre["serializer"].get("importable"):
        print("REFUSING TO RUN: the proven serializer is not importable in "
              "this image. That is the A5 defect's own failure mode and is "
              "never worked around at runtime.", flush=True)
        return 1

    if not frozen:
        print("REFUSING TO RUN: the spec is not frozen.", flush=True)
        return 1

    # The real runner. DISARMED it stops at the final pre-POST refusal; armed
    # and gated, it places the one authorized order.
    rec = RUN.run_experiment(
        observe_s=float(os.getenv("PMUS_EXEC1XR2_OBSERVE_S", "")
                        or RUN.OBSERVE_S),
        book_wait_s=float(os.getenv("PMUS_EXEC1XR2_BOOK_WAIT_S", "")
                          or RUN.BOOK_WAIT_S),
        max_preopen_wait_s=float(
            os.getenv("PMUS_EXEC1XR2_MAX_PREOPEN_WAIT_S", "")
            or RUN.MAX_PREOPEN_WAIT_S))
    print("EXEC1XR2W_SUMMARY " + json.dumps({
        "probe": X.PROBE, "fingerprint": X.XR2W_FINGERPRINT,
        "orders_submitted": rec.get("orders_submitted"),
        "stopped_at": rec.get("stopped_at"),
        "reached_final_gate": rec.get("reached_final_gate", False),
        "refusals": rec.get("refusals"),
        "preopen_wait": rec.get("preopen_wait"),
        "tte_checks": rec.get("tte_checks"),
        "matched_pair_economics": "N/A",
        "VERDICT": ("ORDER PLACED" if rec.get("orders_submitted")
                    else "NO ORDER / %s" % rec.get("stopped_at")),
    }, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
