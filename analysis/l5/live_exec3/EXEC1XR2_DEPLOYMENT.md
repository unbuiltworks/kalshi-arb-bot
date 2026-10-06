# LIVE-L5-EXEC-1-XR2 — DEPLOYED, DISARMED

**NEW FROZEN FINGERPRINT**
`0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f`

**DEPLOYED SHA** `44cbdc8117866976f128103defff6db83bfa1481`
**CMD** `python3 -m scripts.l5_exec1xr2_run`
`deploy/l5_exec1xr2/Dockerfile`, service `l5-live-probe-oneshot`,
restartPolicyType **NEVER**.

Derived from XR `f3e54d82…c5e9`, through it XR-X `d2f32276…6c79` and EXEC-1
`b1dbe7f1…2e06`; all three re-verified in the container at every boot.
`correction_count: 1`. Max possible loss still **$0.0099** on 0.01 shares.
No order, no preview, no signing, no venue mutation. matched-pair economics
**N/A**. **Not authorized yet.**

Attempt 3 is closed as **TIMING SKIP / NO SUBMISSION / ZERO EXECUTION
OBSERVATIONS**. Its latch record at
`…/xr-a3-2026-10-06-03997f04.efc07ec0b654b29f.submitted` was neither
consumed nor deleted; it remains **unspent** on the volume.

## The five owner values are cleared

All five are now empty strings on the service. The service is DISARMED, and
the 18:05Z boot proves it on the live path: `armed: false`, pin
`set: false`, refusals `["SIDE_RULE_INPUT_MISSING", "DISARMED",
"FINGERPRINT_NOT_PINNED_BY_OWNER"]`.

Honest limitation: the Railway MCP surface has no delete-variable call, so
the five **names** still exist with empty values. Empty is as disarming as
absent (`PMUS_EXEC1_LIVE != "YES"`, no session id, pin mismatch), but if you
want the rows gone, remove them in the dashboard.

## The one correction

THE INVARIANT: **no real order submission may occur when TTE <= 120.0 s.**
Enforced three times — defence in depth, not replacement:

1. **at run start** — XR's check, unchanged;
2. **immediately before durable latch consumption** — the check that
   protects the latch, since a refusal there costs nothing;
3. **immediately before `POST /v1/orders`** — closes the gap between the
   latch and the wire.

Unknown or unreadable TTE counts as past the boundary at all three.
`orders.submit_one` gained an optional `guard` callable, invoked on both
sides of `latch.consume()`. A refusal at (2) leaves the latch intact; a
refusal at (3) spends the latch and sends nothing — the safe direction, since
a spent latch loses a run while a late POST breaks the envelope.

## The operational fix

XR had to be deployed INTO an open window, so the experiment depended on a
chat worker waking at the right second and a redeploy finishing in time —
exactly how Attempt 3 was lost. XR2 can start **before** the frozen window
and waits for it:

* it waits for the single slug in `PMUS_EXEC1_GATE_EVIDENCE` and no other;
* **no discovery, no substitution, no roll-forward**; a missed window is a
  SKIP;
* bounded by `MAX_PREOPEN_WAIT_S`;
* an **empty** `PMUS_EXEC1_GATE_EVIDENCE` is REFUSED, because XR's fallback
  to "the current window" was itself a substitution;
* a test asserts the runner module contains no `slug_for`, `wanted_slugs`,
  `window_start()` or next-window arithmetic.

## Call graph to the POST

```
scripts.l5_exec1xr2_run:main
 └─ analysis.l5.live_exec3.runner:run_experiment
     ├─ frozen_slug()                  ← the ONE market, from the env only
     ├─ _await_window()                ← may start before it opens
     ├─ [CHECK 1] T.placement_allowed() at run start
     └─ orders.submit_one(guard=make_tte_guard(...))
          ├─ POST /v1/order/preview → l5_preview_gate.require_echo
          ├─ [CHECK 2] guard()        ← refusal here does NOT spend the latch
          ├─ latch.consume()          ← durable, on the volume
          ├─ [CHECK 3] guard()        ← refusal here spends it, sends nothing
          └─ POST /v1/orders
```

## Tests — 86 new, 390 across six suites

The boundary and race cases, driven with a scripted clock because the gap is
milliseconds in production:

| case | result |
|---|---|
| initial 121 s → pre-POST 119.9 s | REFUSE, latch **unspent**, no POST |
| initial 121 s → pre-POST **exactly 120.0 s** | REFUSE (`<=`, no allowance) |
| initial 121 s → pre-POST 120.5 s | eligible, exactly one POST |
| TTE unknown mid-run at the guard | REFUSE, latch unspent, no POST |
| slow preview crossing the boundary | REFUSE; the preview went out, the submit did not |
| guard called per submission | exactly twice |
| refusal at check 3 | latch spent, **zero** POSTs |
| check 1 alone (71.9 s) | still refuses on its own |

Plus: the pre-open wait polls and then proceeds; an already-open window waits
0; the wait bound exhausts into a SKIP; unreadable window start does not
wait; empty gate evidence refuses; a closed window names only that slug; 16
untouched behaviour keys are asserted identical to XR; five foreign arming
namespaces cannot arm it; max loss is still $0.0099.

## Production boots so far

| boot | slug | preopen wait | TTE at run start | pre-POST recheck | verdict |
|---|---|---|---|---|---|
| 17:42:25Z | …1730z | `waited_s 0.0`, polls 0 | 154.643 s, allowed | **ok, 153.535 s** | NO ORDER / FINAL_PRE_POST_REFUSAL |
| 18:05:20Z | …1800z | `waited_s 0.0`, polls 0 | 579.754 s, allowed | **ok, 578.658 s** | NO ORDER / FINAL_PRE_POST_REFUSAL |

Both reached the real final gate on live data — 17:42 read
`minimumTradeQty 0.01`, tick `0.01`, AuthBook 0.0100/0.0200 at age 0.204 s,
spread 0.0100, our price 0.0100 at offset 0, depth ahead 11137.66
(anonymous) — and both stopped only on the owner's two refusals.

**The pre-open wait, proven in production.** A third boot closed the gap the
first two left open.

| | |
|---|---|
| boot | **2026-10-06T18:19:13Z**, deployment `ca33d133-c4c0-4f22-ae5d-39ef47d8cc57` |
| frozen slug | `cpc-btc-updown-15m-2026-10-06-1900z`, `window_start_unix 1791313200` |
| pre-open detection | `no_discovery: true`, `no_substitution: true`, `no_roll_forward: true` |
| **actual waited duration** | **2450.1 s (40 min 50 s) over 490 polls** |
| window-open transition | `opened: true`, `tte_at_open_s 896.27` |
| TTE at run start | 896.27 s, allowed (cutoff 120.0) |
| AuthBook | bid/ask **0.4900/0.5000**, **age 0.012 s**, two-sided, 1st try, spread 0.0100 |
| price at offset 0 | 0.4900, qty 0.01, depth ahead 818.13 (anonymous) |
| final pre-POST TTE guard | **ok at 896.149 s**, `invariant: no submission when TTE <= 120.0s` |
| refusals | `SIDE_RULE_INPUT_MISSING`, `DISARMED`, `FINGERPRINT_NOT_PINNED_BY_OWNER` |
| `orders_submitted` | **0** |
| durable latch | **unspent** — unscoped, no session id, no file created |
| deadman | `no submission was attempted, so there is nothing that could be resting` |
| venue mutation | none: the record shows no preview and no POST |

Fingerprint in-container `0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f`
with `xr2_frozen: true`; `RAILWAY_GIT_COMMIT_SHA
44cbdc8117866976f128103defff6db83bfa1481`.

The experiment no longer depends on a chat worker waking on the right second
or on a redeploy finishing after market open: the process was already running
41 minutes before the window and entered eligibility when it opened.
