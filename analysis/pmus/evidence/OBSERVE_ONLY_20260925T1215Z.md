# OBSERVE-ONLY — 2026-09-25 1215z — **PASSED**, after the quantization change

Deployment `1939711d`, commit `dfd89ee` over `5d1cd90`. Container started
12:14:01Z, rolled forward to `cpc-btc-updown-15m-2026-09-25-1215z`, ran the
window end to end, report printed 12:30:06Z. `L5_OBSERVE_ONLY=YES`, no
`PMUS_Q1_*`, `PMUS_PREFLIGHT_ACCOUNT=d51b44a3fa83`.

## The report, verbatim

```json
{"LABEL": "LIVE_OBSERVE_ONLY", "market": "cpc-btc-updown-15m-2026-09-25-1215z",
 "stage_reached": "done", "authorization_claimed": false,
 "post_capable": false, "transport": "NULL",
 "l5_feed": {"polls": 177, "polls_ok": 177, "polls_failed": 0,
             "raw_rows": 17700, "rows_in_window": 8950, "new_actions": 118,
             "last_ok_utc": "2026-09-25T12:29:58.850301+00:00",
             "last_error": null, "healthy": true},
 "book_feed": {"connected": false, "frames": 10745, "book_states": 2000,
               "last_book_age_s": 5.338, "fresh": false, "errors": [],
               "stop_reason": "local_cancellation", "source": "AUTHENTICATED_WS"},
 "actions_handled": 118, "l5_native_decisions": 118,
 "pmus_translations_ok": 52, "overlay_refusals": 0, "would_submit": 52,
 "refused_by_layer": {"PMUS_TRANSLATION": 66},
 "refused_reason": {"STRICT_NOT_EVALUABLE": 66},
 "armed": false, "submitted_live": 0}
```

Three log lines total: the volume mount, the container start, and this. No
`L5_PROBE_ERROR`, no traceback.

## Result

```
L5 ACTIONS:            118
WOULD_SUBMIT:          52            (non-zero, which is the gate)
L5_NATIVE DECISIONS:   118
PMUS TRANSLATIONS:     52
REFUSALS:              66 STRICT_NOT_EVALUABLE, all at PMUS_TRANSLATION
OVERLAY REFUSALS:      0
L5 POLLS OK/FAILED:    177 / 0
RAW/IN-WINDOW/NEW:     17700 / 8950 / 118
BOOK FRAMES/STATES:    10745 / 2000   AUTHENTICATED_WS, 0 errors
EARLY EXIT/ERROR:      none — stage_reached "done"
POST CAPABILITY:       NO            AUTHORIZATION CLAIMED: NO
ARMED:                 false         SUBMITTED_LIVE: 0
```

118 = 52 + 66. Every action accounted for, and the execution path ran clean
under the changed order representation.

The two figures that look wrong and are not: `book_states 2000` is the ring
buffer's capacity against 10745 frames received, and `connected: false` /
`fresh: false` / `age 5.338s` are read *after* `finally: feed.stop()`, with
`stop_reason: local_cancellation` — the run closed its own feed.

## Recorder, adjacent window

The recorder's `1215z` `L5_WINDOW` line had not been retrieved at the time of
writing; its `1200z` line is the nearest full window and is offered as
context, **not** as a same-interval comparison:

`1200z` — 74 L5 actions, 176 polls / 0 failures, 31 would-orders,
`PMUS_TRANSLATION: 43` / `STRICT_NOT_EVALUABLE: 43`, coverage 99.5%,
subscription +4.8s, 7120 usable book states, preflight PASSED on the third
attempt with no blockers, `submitted_live: 0`, `armed: false`.

The venue is visibly busier than overnight: 8950 rows in window against 3564
at 0115z.

## What this window does and does not prove

**Does:** the production decision path — authenticated WS -> shared
`l5_window_actions` -> frozen L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY
-> would-submit -> transport refusal — runs clean for a full window on the
build that carries the quantization change and the submit forensics. No
regression in reachability: L5 actions are ingested, decisions are readable,
translations succeed, and 52 intents reach the transport.

**Does not:** exercise the quantization. Observe-only is DISARMED, so it runs
no preflight, so the venue's grid is never established and
`floor_to_increment` is never called — the record carries
`quantized: false`, `quantity_increment: null`, by design, so that a
disarmed run is not blocked by a constraint it cannot read. Flooring is
proven in test against the exact Probe 03 numbers
(`0.4066877541798463` + `0.01` -> `0.40` -> echo accepted -> reaches
`/v1/orders`). **Its first live exercise will be Probe 04.**

## State

`L5_OBSERVE_ONLY=YES`, `PMUS_PREFLIGHT_ACCOUNT=d51b44a3fa83`, no live
authorization variables. Deployment `1939711d` has exited;
`restartPolicyType: NEVER`. Nothing armed, no order, no money moved.
