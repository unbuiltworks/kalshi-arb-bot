"""LIVE readiness proof: the corrected provider against the REAL public feed.

WHY THIS EXISTS. The failed cohort's tape proves the readiness race, but it
cannot EXCLUDE an additional connection-level problem: it records no
websocket connect or handshake error, only "token never seen". So the fix
is not considered proven by the suite alone. This runs the production
readiness path against the real public websocket and shows an actual live
BTC15m market reaching BOTH_TOKENS_STREAMING on its exact UP/DOWN token
ids.

ORDERING IS DISABLED BY CONSTRUCTION, not by a flag:

  * it imports NO transport, NO latch, NO order body and NO credential
    module -- `assert_no_order_path_imported()` checks `sys.modules` after
    the work is done and fails if any appeared;
  * it reads `PMUS_E1_*` for nothing. It does not arm, and arming it would
    change nothing here;
  * the only network it performs is the public gamma window lookup and the
    public market websocket, both unauthenticated reads.

It exits 0 on BOTH_TOKENS_STREAMING, 2 on a bounded readiness timeout
(reporting which of the four reasons), 3 if no window could be identified.

Run:
    python3 -m scripts.l5_e1_ws_readiness_check
"""
from __future__ import annotations

import json
import sys
import time

from analysis.l5.e1_cohort import live_book as LB
from analysis.l5.e1_cohort import provider as PV
from analysis.l5.e1_cohort import selector as SEL

#: anything here means this check reached a money-moving module. It must
#: not, and the check proves it rather than asserting it in prose.
FORBIDDEN_MODULES = (
    "analysis.l5.live_exec2.orders",
    "analysis.l5.e1_cohort.latch",
    "src.l5_order_body",
    "src.l5_preview_gate",
    "src.pmus_auth",
)


def assert_no_order_path_imported() -> dict:
    present = sorted(m for m in FORBIDDEN_MODULES if m in sys.modules)
    if present:
        raise SystemExit(
            "REFUSED: an order-capable module is loaded in a read-only "
            "readiness check: %r" % (present,))
    return {"forbidden_modules_loaded": [], "ordering_disabled": True}


def candidate_starts(now: float, n: int = 4) -> list:
    out = [SEL.window_start_for(now)]
    out += [s for s in SEL.next_window_starts(now, n) if s not in out]
    return out


def main() -> int:
    out: dict = {"check": "E1_WS_READINESS_LIVE",
                 "timeout_s": PV.WS_READY_TIMEOUT_S,
                 "poll_s": PV.WS_READY_POLL_S,
                 "ordering_disabled": True,
                 "venue_post": "none",
                 "credentials_used": False}

    now = time.time()
    pin = None
    tried = []
    for start in candidate_starts(now):
        try:
            pin = SEL.select_window(start)
            break
        except Exception as exc:                            # noqa: BLE001
            tried.append({"window_start": start,
                          "refused": "%s: %s" % (type(exc).__name__, exc)})
    out["windows_tried"] = tried
    if pin is None:
        out["result"] = "NO_IDENTIFIABLE_WINDOW"
        out.update(assert_no_order_path_imported())
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return 3

    out["slug"] = pin["slug"]
    out["condition_id"] = str(pin.get("condition_id")
                              or pin.get("market_id") or "")
    out["token_up"] = str(pin["token_up"])
    out["token_down"] = str(pin["token_down"])

    t0 = time.time()
    try:
        proof = PV.await_ws_both_tokens_ready(
            LB.health_fn, pin,
            now_fn=time.time, sleep_fn=time.sleep,
            timeout_s=PV.WS_READY_TIMEOUT_S, poll_s=PV.WS_READY_POLL_S)
    except PV.FeedNotReady as exc:
        out["result"] = "FEED_NOT_READY"
        out["reason"] = str(exc)
        out["elapsed_s"] = round(time.time() - t0, 3)
        h = LB.health_fn(pin)
        out["health_frames"] = h.get("frames")
        out["health_market"] = h.get("market")
        out["health_tokens_last_seen_unix"] = h.get("tokens_last_seen_unix")
        out.update(assert_no_order_path_imported())
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return 2

    out["result"] = "BOTH_TOKENS_STREAMING"
    out["elapsed_s"] = round(time.time() - t0, 3)
    out["readiness"] = proof.get("readiness")
    out["proven_for_the_selected_market"] = proof.get(
        "proven_for_the_selected_market")
    h = LB.health_fn(pin)
    seen = {str(k): v for k, v in (h.get("tokens_last_seen_unix")
                                   or {}).items()}
    out["health_market"] = h.get("market")
    out["health_frames"] = h.get("frames")
    out["health_frames_applied"] = h.get("frames_applied")
    out["health_frames_refused_other_market"] = h.get(
        "frames_refused_other_market")
    out["tokens_last_seen_unix"] = seen
    out["exact_token_ids_confirmed"] = (
        sorted(seen) == sorted([str(pin["token_up"]),
                                str(pin["token_down"])]))
    out.update(assert_no_order_path_imported())
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
