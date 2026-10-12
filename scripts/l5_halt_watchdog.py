"""The watchdog process: serve the latch, sweep the venue, act.

It runs on an EXISTING service. It needs no executor, no ledger, no order
body and no arming key -- it holds `l5_watchdog.CancelOnlyTransport`, whose
only mutating route is `POST /v1/order/{id}/cancel`.

Loop, once every L5_WATCHDOG_POLL_S:
  * read `GET /v1/orders/open`;
  * an order resting longer than L5_WATCHDOG_MAX_ORDER_AGE_S is an order the
    executor is not managing -- latch the halt, then (with
    L5_WATCHDOG_CANCEL=YES) cancel exactly that venue id and verify by
    re-reading it;
  * a clean sweep refreshes the positive "no halt" attestation, which is the
    only thing that lets the executor trade at all;
  * a failed read writes NOTHING, so the attestation ages out and the
    executor fails closed within HaltGate's max_age_s.

It restarts safely: the latch lives on the volume, so a restarted watchdog
finds the halt it asserted before and `heartbeat_no_halt` refuses to
overwrite it.
"""
from __future__ import annotations

import json
import os
import sys
import time

from src import l5_halt_gate as HG
from src import l5_halt_server as SRV
from src import l5_watchdog as W
from src import pmus_auth as A


def emit(tag: str, payload) -> None:
    print("%s %s" % (tag, json.dumps(payload, default=str, sort_keys=True)),
          flush=True)


def main() -> int:
    root = os.getenv("L5_WATCHDOG_STATE_DIR", "/data")
    port = int(os.getenv("PORT", "8080"))
    poll_s = float(os.getenv("L5_WATCHDOG_POLL_S", "20"))
    store = HG.FileStore(root)
    SRV.serve(store, port=port)
    wd = W.from_env(store=store)
    emit("L5_WATCHDOG_BOOT", {
        "state_dir": root, "port": port, "poll_s": poll_s,
        "max_order_age_s": wd.max_order_age_s,
        "cancel_enabled": wd.cancel_enabled,
        "credentials": A.has_credentials(),
        "serves": "/" + HG.HALT_KEY,
        "labels": ("GET-only server; the only mutating route this process "
                   "can build is POST /v1/order/{id}/cancel"),
    })
    if not A.has_credentials():
        # Cannot see the venue, so cannot attest. Serving the latch and
        # attesting nothing is the fail-closed shape: the executor stops.
        emit("L5_WATCHDOG_ALARM", {
            "kind": "no_credentials",
            "labels": ("serving the latch read-only and attesting nothing; "
                       "the executor will fail closed when the record "
                       "goes stale")})
    while True:
        try:
            emit("L5_WATCHDOG_SWEEP", wd.sweep())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # never attest on an error path, and never die: a watchdog that
            # exits is a watchdog that stops watching.
            emit("L5_WATCHDOG_ALARM", {"kind": "sweep_raised",
                                       "error": type(exc).__name__,
                                       "detail": str(exc)[:200]})
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
