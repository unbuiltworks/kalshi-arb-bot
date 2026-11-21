# OBSERVE-ONLY verification — 2026-09-25 0115z — **PASSED**

Service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`),
deployment `bf0004c2`, commit `df5d7bb` over `724b18e`. `L5_OBSERVE_ONLY=YES`,
no `PMUS_Q1_*` variables on the service. Container started 01:10:33Z, rolled
forward to `cpc-btc-updown-15m-2026-09-25-0115z`, and ran that window end to
end. Report printed 01:30:06Z. `stage_reached: "done"`.

## The report, verbatim

```json
{"LABEL": "LIVE_OBSERVE_ONLY", "market": "cpc-btc-updown-15m-2026-09-25-0115z",
 "stage_reached": "done", "authorization_claimed": false, "post_capable": false,
 "transport": "NULL",
 "l5_feed": {"polls": 177, "polls_ok": 177, "polls_failed": 0,
             "raw_rows": 17700, "rows_in_window": 3564, "new_actions": 33,
             "last_ok_utc": "2026-09-25T01:30:00.062624+00:00",
             "last_error": null, "healthy": true},
 "book_feed": {"market": "cpc-btc-updown-15m-2026-09-25-0115z",
               "connected": false, "frames": 9371, "book_states": 2000,
               "last_book_age_s": 9.565, "fresh": false, "errors": [],
               "stop_reason": "local_cancellation", "source": "AUTHENTICATED_WS"},
 "actions_handled": 33, "l5_native_decisions": 33, "pmus_translations_ok": 21,
 "overlay_refusals": 0, "would_submit": 21,
 "refused_by_layer": {"PMUS_TRANSLATION": 12},
 "refused_reason": {"STRICT_NOT_EVALUABLE": 12},
 "armed": false, "submitted_live": 0,
 "live_limits": {"max_usd_per_market": "5", "max_abs_residual": "10",
                 "max_total_exposure_usd": "5", "max_concurrent_markets": 1,
                 "max_live_orders": 1, "daily_realized_loss_halt_usd": "5",
                 "band": null},
 "labels": "OBSERVE ONLY. No authorization was claimed, no PmusTransport was constructed, and the executor held NullTransport, which has no HTTP client. A would-submit is an intent the transport refused, never a fill."}
```

Nothing else in the container log but the volume mount and `Starting Container`.
No `L5_PROBE_ERROR`. No traceback.

## Two independent processes, the same window, the same numbers

The recorder ran `0115z` in its own container, from its own subscription, with
its own book. It did not know the one-shot existed.

| | observe-only (`bf0004c2`) | recorder (`c34af3d9`) |
|---|---|---|
| L5 actions | **33** | **33** |
| L5 polls / failures | **177 / 0** | **177 / 0** |
| would-submit / would-orders | **21** | **21** |
| refused by layer | **`PMUS_TRANSLATION: 12`** | **`PMUS_TRANSLATION: 12`** |
| refusal reason | **`STRICT_NOT_EVALUABLE: 12`** | **`STRICT_NOT_EVALUABLE: 12`** |
| submitted_live | **0** | **0** |
| armed | **false** | **false** |

33 = 21 + 12, and every figure agrees across two processes that share only the
code. That is the corroboration the gate existed to produce.

The recorder adds, for the same window: book coverage 99.9%, max book gap 0.5s,
subscription +0.5s vs open, 6740 usable book states, 1501 authenticated trades,
action age p50 18.4s / p90 27.11s / max 29.24s, 0 stale-action refusals,
`preflight: PASSED` at 49.6s on the first attempt with `preflight_blockers: []`.

## Checklist

```
POST CAPABILITY:              NO      (post_capable false, transport "NULL")
AUTHORIZATION CLAIMED:        NO      (observe() never calls auth.claim())
ARMED:                        false
SUBMITTED_LIVE:               0
AUTH WS SUSTAINED:            YES     (9371 frames over ~19.5 min, 0 errors)
BOOK FRAMES/STATES:           9371 frames / 2000 book states
L5 POLLS OK/FAILED:           177 / 0
RAW/TARGET/NEW/READABLE:      17700 / 3564 / 33 / 33
RECORDER ACTIONS SAME INTERVAL: 33 actions, 177 polls, 0 failures, 21 would-orders
L5_NATIVE DECISIONS:          33
PMUS TRANSLATIONS:            21
WOULD_SUBMITS:                21
REFUSALS BY REASON:           STRICT_NOT_EVALUABLE 12 (all at PMUS_TRANSLATION);
                              SAFETY_OVERLAY 0
EARLY EXIT/ERROR:             none. stage_reached "done", no L5_PROBE_ERROR
READY FOR PROBE 03:           YES
```

Two figures that look like failures and are not:

- **`book_states: 2000`** is the feed's ring-buffer capacity, not a shortfall.
  9371 frames arrived; the buffer holds the last 2000 states by design.
- **`connected: false`, `fresh: false`, `last_book_age_s: 9.565`** are read
  *after* the `finally: feed.stop()` block, so they describe the feed after
  shutdown. `stop_reason: "local_cancellation"` says the run closed it, not
  that the venue dropped it. During the window the recorder measured the same
  stream at 99.9% coverage with a 0.5s maximum gap.

## What this run proves that no earlier one did

The whole pre-execution path ran in production, for a full window, against a
live venue, and reported itself: authenticated WS -> shared
`l5_window_actions` -> frozen L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY
-> would-submit -> transport refusal. Probes 01 and 02 each spent a real-money
authorization failing somewhere in that path without saying where. This cost
nothing and said everything.

The three defects the gate found, in order: the book feed's missing `asyncio`
import (Probe 01), the packaging hole that killed Probe 02 silently while
armed, and the report writer that could only run when there was nothing to
report. None of them was reachable by a test against the working tree. All
three now have regression tests.

## State

`L5_OBSERVE_ONLY=YES` still set. No `PMUS_Q1_*` variables anywhere. Nothing
armed, no authorization claimed, no order, no money moved. Deployment
`bf0004c2` has exited; `restartPolicyType: NEVER`. Recorder untouched.
