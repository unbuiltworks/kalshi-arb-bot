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


#: how many order-state reads to time. Small on purpose: this measures
#: latency, it does not load-test someone else's venue.
SAMPLES = 6
SAMPLE_PAUSE_S = 1.0


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

    # ---------------------------------------------------------------- (3)
    # LATENCY, FRESHNESS, ERRORS AND RATE LIMITS, MEASURED RATHER THAN
    # ASSUMED. The 5-second staleness bound in `order_state` is a judgement
    # I wrote without data; these samples are the data. A 429 or any other
    # HTTP status is reported with its code, because "it failed" is not a
    # measurement.
    samples = []
    for i in range(SAMPLES):
        a = time.time()
        v = OS.read_order_state(reader, slugs=slugs,
                                outcome_up="OUTCOME_SIDE_YES",
                                now=a, now_fn=time.time)
        samples.append({"i": i, "latency_s": round(time.time() - a, 4),
                        "ok": v.get("ok"), "reason": v.get("reason"),
                        "age_s": round(float(v.get("age_s") or 0), 4),
                        "n_outstanding": v.get("n_outstanding")})
        if i + 1 < SAMPLES:
            time.sleep(SAMPLE_PAUSE_S)
    lats = sorted(x["latency_s"] for x in samples)
    oks = [x for x in samples if x["ok"]]
    print("LATENCY_SAMPLES %s" % json.dumps(samples))
    print("LATENCY_SUMMARY %s" % json.dumps({
        "n": len(samples), "ok": len(oks), "errors": len(samples) - len(oks),
        "min_s": lats[0], "median_s": lats[len(lats) // 2],
        "max_s": lats[-1],
        "max_age_s_bound": OS.MAX_ORDER_STATE_AGE_S,
        "worst_sample_within_the_bound":
            lats[-1] <= OS.MAX_ORDER_STATE_AGE_S,
        "headroom_s": round(OS.MAX_ORDER_STATE_AGE_S - lats[-1], 4),
        "reasons": sorted({str(x["reason"]) for x in samples}),
    }))
    # the raw statuses, so a 429 or a 5xx is visible as itself
    for path in (OS.OPEN_PATH, OS.ORDERS_PATH):
        a = time.time()
        try:
            body = reader(path)
            print("RAW_GET %s" % json.dumps({
                "path": path, "status": 200,
                "latency_s": round(time.time() - a, 4),
                "rows": len(OS.rows(body, "orders", "data") or []),
            }))
        except Exception as exc:                               # noqa: BLE001
            print("RAW_GET %s" % json.dumps({
                "path": path, "error": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "latency_s": round(time.time() - a, 4),
                "is_rate_limit": getattr(exc, "code", None) == 429,
            }, default=str))

    # ---------------------------------------------------------------- (4)
    # WOULD THE ENGINE REFUSE? Asked of the REAL `runner.eligible`, on the
    # REAL snapshot shape, with the order state replaced by a synthetic
    # venue answer. No network, no transport, no order body: eligibility is
    # decided entirely before any of that exists, which is why it can be
    # interrogated this way at all.
    from analysis.l5.e1_cohort import runner as RUN
    from analysis.l5.e1_cohort import stopping as STOP

    def _probe_refusal(label, rows_open, rows_hist=None, pending=()):
        def _r(path):
            if path == OS.OPEN_PATH:
                return {"orders": list(rows_open)}
            return {"orders": list(rows_open if rows_hist is None
                                   else rows_hist)}
        now = time.time()
        v = OS.read_order_state(_r, slugs=slugs,
                                outcome_up="OUTCOME_SIDE_YES",
                                now=now, pending_ids=pending)
        snap = {
            "window": 0, "window_open": True, "within_handover_tte": True,
            "market": slugs[0], "pin": {"slug": slugs[0]},
            "qty": "0.13", "first_leg_price": "0.50",
            "order_state": v,
            "our_outstanding_orders": OS.outstanding_for_snapshot(v),
            "our_open_residual": OS.residual_for_snapshot(v),
        }
        el = RUN.eligible(snap, STOP.CohortTracker(), now=now)
        e3 = [r for r in el["refusals"] if str(r).startswith("E3_")]
        print("WOULD_REFUSE %s" % json.dumps({
            "case": label,
            "our_outstanding_orders": snap["our_outstanding_orders"],
            "eligible_ok": el["ok"],
            "e3_refusals": e3,
            "refused": not el["ok"],
        }, default=str))
        return el

    one = {"id": "SYNTHETIC-RESTING", "marketSlug": slugs[0],
           "outcomeSide": "OUTCOME_SIDE_NO", "state": "ORDER_STATE_NEW",
           "quantity": "0.13", "cumQuantity": "0",
           "leavesQuantity": "0.13", "price": {"value": "0.55"}}
    part = dict(one, id="SYNTHETIC-PARTIAL",
                state="ORDER_STATE_PARTIALLY_FILLED",
                cumQuantity="0.05", leavesQuantity="0.08")
    filled = dict(one, id="SYNTHETIC-FILLED", state="ORDER_STATE_FILLED",
                  cumQuantity="0.13", leavesQuantity="0")
    _probe_refusal("ONE_RESTING_ORDER", [one])
    _probe_refusal("TWENTY_RESTING_ORDERS",
                   [dict(one, id="SYNTH%02d" % i) for i in range(20)])
    _probe_refusal("PARTIALLY_FILLED_ORDER", [part])
    _probe_refusal("FILLED_FIRST_LEG_RESIDUAL", [], [filled])
    _probe_refusal("UNIDENTIFIED_SUBMISSION", [], [],
                   pending=[OS.LOCAL_INTENT + "w0:%s:UP:0.50" % slugs[0]])

    # ---------------------------------------------------------------- (5)
    # FAIL-CLOSED, EXERCISED. Each of these is a real failure mode of the
    # real reader, driven locally so the venue is not abused to produce it.
    def _fail(label, bad_reader, **kw):
        v = OS.read_order_state(bad_reader, slugs=slugs,
                                outcome_up="OUTCOME_SIDE_YES",
                                now=time.time(), **kw)
        r = OS.reconcile_at_startup(v)
        print("FAIL_CLOSED %s" % json.dumps({
            "case": label, "ok": v.get("ok"), "reason": v.get("reason"),
            "outstanding_published":
                OS.outstanding_for_snapshot(v),
            "outstanding_is_truthy": bool(OS.outstanding_for_snapshot(v)),
            "startup_may_submit": r["may_submit"],
        }, default=str))

    def _raise(path):
        raise OSError("synthetic connection reset")

    def _garbage(path):
        return {"unexpectedShape": True}

    clock = {"t": time.time()}

    def _slow(path):
        clock["t"] += 4.0
        return {"orders": []}

    def _race(path):
        if path == OS.OPEN_PATH:
            return {"orders": [one]}
        return {"orders": [dict(one, state="ORDER_STATE_CANCELED",
                                leavesQuantity="0")]}

    _fail("UNREADABLE", _raise)
    _fail("UNPARSABLE", _garbage)
    _fail("CANCELLATION_RACE", _race)
    v = OS.read_order_state(_slow, slugs=slugs,
                            outcome_up="OUTCOME_SIDE_YES",
                            now=clock["t"], now_fn=lambda: clock["t"])
    print("FAIL_CLOSED %s" % json.dumps({
        "case": "STALE", "ok": v.get("ok"), "reason": v.get("reason"),
        "outstanding_is_truthy": bool(OS.outstanding_for_snapshot(v)),
        "startup_may_submit": OS.reconcile_at_startup(v)["may_submit"],
    }, default=str))

    # ---------------------------------------------------------------- (6)
    # THE COHORT'S OWN AUTHORITATIVE LEDGER, read from the durable volume.
    # Append-only; this opens it for reading and writes nothing.
    try:
        from analysis.l5.e1_cohort import latch as LATCH
        from analysis.l5.e1_cohort.progress import CohortProgress
        prog = CohortProgress(LATCH.LATCH_DIR)
        print("LEDGER %s" % json.dumps({
            "state_dir": LATCH.LATCH_DIR,
            "session_id": prog.session_id,
            "windows_used": prog.windows_used(),
            "windows_remaining": prog.windows_remaining(),
            "slugs_used": prog.slugs_used(),
            "placements": prog.placements(),
            "realized_loss_usd": str(prog.realized_loss_usd()),
            "unresolved_residuals": prog.unresolved_residuals(),
            "events": len(prog.events),
        }, default=str))
    except Exception as exc:                                   # noqa: BLE001
        print("LEDGER_ERROR %s" % json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:200]}))
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
