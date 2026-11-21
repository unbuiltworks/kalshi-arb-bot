# OBSERVE-ONLY verification — 2026-09-25 00:20Z — packaging defect FIXED, run crashed writing its report

Service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`),
deployment `80570eaf`, commit `7b6e698` (promote marker) over `b27559a`
(the import-closure fix). `L5_OBSERVE_ONLY=YES`, no `PMUS_Q1_*` variables
on the service. Target window `cpc-btc-updown-15m-2026-09-25-0015z`.

## Verdict

**The packaging defect is fixed and proven fixed in the container.** The
`ImportError` is gone, and the process ran the observing loop for the full
9m14s from container start to window close.

**A second, different defect then destroyed the report.** At the very end,
after the loop completed, `observe()` raised

    TypeError: object of type 'int' has no len()

constructing its summary. The numbers the run had just spent a window
collecting were never printed.

No deployment was made in response. Per standing instruction, work stopped
at this report.

## Exact stdout — the complete container log, verbatim

```
[2026-09-25T00:20:46.813322685Z] info: Mounting volume on: /var/lib/containers/railwayapp/bind-mounts/d8dfe6a5-07a2-453f-81af-9a30f6e05e34/vol_rkz2fj1g66byw2dy
[2026-09-25T00:20:46.813364578Z] info: Starting Container
[2026-09-25T00:30:06.1236Z] error: Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/app/scripts/l5_pmus_live_oneshot.py", line 517, in <module>
    raise SystemExit(main())
  File "/app/scripts/l5_pmus_live_oneshot.py", line 507, in main
    rep = observe()
  File "/app/scripts/l5_pmus_live_oneshot.py", line 490, in observe
    "would_submit": len(summary.get("would_submit") or []),
TypeError: object of type 'int' has no len()
```

Nothing between 00:20:46 and 00:30:06: the observing loop journals, it does
not print per action.

## Root cause

`l5_pmus_live_wiring.summarize` returns `would_submit` as a **count**:

    return {"would_submit": len(would_submit), ...}

`observe()` line 490 takes `len()` of it again. Reproduced locally against
records shaped exactly as the DISARMED path produces them:

| records | `summarize()["would_submit"]` | line 490 |
|---|---|---|
| 0 | `0` (int) | `0 or []` -> `[]` -> `len([])` = **0, no error** |
| 1 | `1` (int) | **TypeError: object of type 'int' has no len()** |
| 3 | `3` (int) | **TypeError** |

The `or []` is what hid it: at zero the falsy int is replaced by a list and
the expression happens to give the right answer. It raises **only when
`would_submit` is non-zero**.

This is the single occurrence in the runner. `run()` -- the live Probe 03
path -- does not contain this expression and is not affected.

Why local tests missed it: `tests/test_l5_live_oneshot.py:872` does assert
on `rep["would_submit"]`, but every observe() fixture produces zero
would-submits, so every test took the masked branch.

## What the crash itself proves

`summarize` increments `would_submit` only for a record whose
`ORDER["reason"] == "NOT_ARMED"` with an intent, and increments
`submitted_live` only for `ORDER["submitted"] is True`. The exception
therefore establishes, without needing the lost report:

- **the import closure is complete** -- `new_l5_actions` imports
  `l5_pmus_realtime_observer` lazily on every poll, and the process
  survived 9m14s of that loop;
- **L5 actions were ingested and were readable**;
- **L5_NATIVE decided, and PMUS_TRANSLATION succeeded**, for at least one
  action -- an intent cannot exist otherwise;
- **the SAFETY_OVERLAY reached the transport and the transport refused**,
  `NOT_ARMED`. A would-submit is by definition an order the transport
  declined.

What it does not give is the counts. Those are on the volume, in the
journal's `observe_action` records, unreadable without a running container.
The `observe_report` record was never written -- the crash is at the dict
construction, one line before `journal.write`.

## Checklist

| gate | required | observed | |
|---|---|---|---|
| IMPORT CLOSURE COMPLETE | YES | no ImportError; 9m14s of the observing loop, which imports the observer every poll | **YES** |
| OBSERVE-ONLY POST CAPABILITY | NO | `NullTransport`; would-submits recorded as `NOT_ARMED`, which is the transport refusing | **NO** |
| AUTHORIZATION CLAIMED | NO | `observe()` never calls `auth.claim()`; no `PMUS_Q1_*` on the service | **NO** |
| AUTH WS SUSTAINED | YES/NO | ran to window close without a feed exception, but the count is in the lost report | **UNPROVEN** |
| BOOK FRAMES / STATES | count | lost with the report | **UNPROVEN** |
| L5 POLLS OK / FAILED | count | lost with the report; >= 1 ok is implied by the intent | **UNPROVEN** |
| RAW / TARGET / NEW / READABLE | count | lost with the report; all >= 1 implied | **UNPROVEN** |
| RECORDER ACTIONS SAME INTERVAL | — | 0015z: **239 actions**, 177 polls, 0 failures, **98 would_orders**, coverage 99.9%, preflight PASSED, armed false | recorder healthy |
| L5_NATIVE DECISIONS | count | >= 1 proven by the exception; exact count lost | **PARTIAL** |
| PMUS TRANSLATIONS | count | >= 1 proven by the exception; exact count lost | **PARTIAL** |
| WOULD-SUBMITS / REFUSALS | count | **>= 1** proven by the exception; exact counts lost | **PARTIAL** |
| EARLY EXIT / ERROR | none | no early exit -- ran to window close -- then TypeError writing the report | **FAIL** |
| ARMED | false | `false`; `observe()` has no arming path | **PASS** |
| SUBMITTED_LIVE | 0 | 0 by construction: `NullTransport` holds no credential and no HTTP client | **PASS** |
| READY FOR PROBE 03 | — | **NO** -- the evidence the gate exists to produce was not produced | **BLOCKED** |

Coverage note: the container started at 00:20:46 and the window closed at
00:30:00, so the run covered the last **9m14s (~62%)** of 0015z. The
recorder's 239 actions / 98 would-orders are the **full** 15 minutes. The
two are not directly comparable, and no comparison is claimed here.

## Recorder, the same and preceding window (`c34af3d9` / `45b8749`)

| window | coverage % | usable states | l5_actions | polls | fails | would_orders | submitted_live | preflight | armed |
|---|---|---|---|---|---|---|---|---|---|
| 0000z | 99.9 | 6903 | 117 | 176 | 0 | 53 | 0 | PASSED | false |
| 0015z | 99.9 | 8393 | 239 | 177 | 0 | 98 | 0 | PASSED | false |

## The correction, not applied

One line, `scripts/l5_pmus_live_oneshot.py:490`:

    -    "would_submit": len(summary.get("would_submit") or []),
    +    "would_submit": summary.get("would_submit", 0),

and a test that drives `observe()` to a **non-zero** would-submit, which no
existing test does. Not written and not deployed: another error appeared,
so work stopped here.

## State left behind

- `L5_OBSERVE_ONLY=YES` still set. No `PMUS_Q1_*` variables. Nothing armed,
  no authorization claimed, no order, no money moved.
- Deployment `80570eaf` has exited; `restartPolicyType: NEVER`, so it does
  not relaunch.
- The recorder is untouched and healthy.
