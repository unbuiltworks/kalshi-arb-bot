"""REQUIREMENT 7: verify the repaired invariant AGAINST ACTUAL VENUE STATE,
read-only.

The new `order_state` module decides whether E1 may open a first leg. Unit
tests prove its rules against fixtures. This proves it against the real
venue -- the same GET paths, the same parsing, the same arithmetic -- and
reports what it WOULD have decided, without arming anything, submitting
anything, or touching the E1 session, latch or progress state.

It imports `order_state` and nothing that can place an order. The transport
is `pmus_auth.ReadOnlyTransport`, which raises PermissionError on any method
but GET.

Expected result right now, after the twenty orders were cancelled and the
cancellation was verified per id: zero outstanding, no residual, zero
aggregate exposure, and `may_submit: True` -- that last value being a
STATEMENT ABOUT VENUE STATE, not an authorization. E1 stays disarmed.
"""
from __future__ import annotations

import json
import time

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import pmus_identity as PI


def main() -> int:
    now = time.time()
    # the current window and its three successors, named the way E1 names
    # them, so the scope rule is exercised exactly as it would be live.
    ws = PI.window_start(now)
    starts = [ws + i * PI.WINDOW_S for i in range(4)]
    slugs = [PI.slug_for(s) for s in starts]

    print("VERIFY_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
    print("SLUGS %s" % json.dumps(slugs))
    print("CAN_READ_VENUE %s" % OS.can_read_venue())

    reader = OS.venue_reader()
    t0 = time.time()
    # the long side, which is what the venue's `price.value` always names.
    view = OS.read_order_state(reader, slugs=slugs,
                               outcome_up="OUTCOME_SIDE_YES",
                               now=t0, now_fn=time.time)
    print("READ_LATENCY_S %.3f" % (time.time() - t0))
    for key in ("ok", "reason", "age_s", "n_known", "n_outstanding",
                "outstanding_ids", "open_ids", "unconfirmed_pending_ids",
                "partially_filled_ids", "filled_ids",
                "net_filled_by_outcome_side", "matched_qty", "residual",
                "open_notional_usd", "filled_notional_usd",
                "aggregate_exposure_usd"):
        print("STATE %s=%s" % (key, json.dumps(view.get(key), default=str)))
    for o in (view.get("orders") or [])[:25]:
        print("ORDER %s" % json.dumps(o, default=str))

    print("OUTSTANDING_FOR_SNAPSHOT %s"
          % json.dumps(OS.outstanding_for_snapshot(view), default=str))
    print("RESIDUAL_FOR_SNAPSHOT %s"
          % json.dumps(OS.residual_for_snapshot(view), default=str))
    rec = OS.reconcile_at_startup(view)
    print("RECONCILE %s" % json.dumps(rec, default=str))
    print("NOTE %s" % json.dumps({
        "this_is_read_only": True,
        "e1_remains_disarmed": True,
        "no_order_was_placed_amended_or_cancelled": True,
        "may_submit_describes_venue_state_not_authorization": True,
        "session_latch_and_progress_untouched": True,
    }))
    print("VERIFY_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
