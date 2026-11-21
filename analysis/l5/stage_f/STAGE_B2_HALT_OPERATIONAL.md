# Emergency HALT — deployed, measured, and the owner-facing procedure

**Executor disarmed throughout. No live trading, no limit changes, no new
services.**

## Deployment evidence

| | |
|---|---|
| **running SHA** | **`17f0656552ed816638421c22913b0ca7c1901615`** |
| deployment | **`ee4fe9aa-58db-4767-be76-9b55a96be4ff`**, SUCCESS |
| branch | `claude/gemini-btc-economic-viability-h3u7zl` (the watchdog's own) |
| build window | 02:41:09Z → 02:42:30Z = **81s** |
| trigger live | `L5_EMERGENCY_HALT_NOW_CONFIG` at 02:42:28Z, `armed: false`, `poll_s: 20.0` |
| rollback | previous watchdog deployment, `canRollback: true` |

**Cherry-pick scope:** exactly three files, **additive only**, 466 insertions
and no deletions. Nothing merged, nothing repointed, the 321-commit branch
untouched.

**The promotion mechanism demonstrated itself.** Pushing the code commit
`644ab221` produced deployment `dd409084` with status **SKIPPED** — because
`watchPatterns` on this service is exactly its `DEPLOY_TRIGGER` file. Pushing
`17f06565`, which appends to that file, built and deployed. So the running
commit is precisely the commit carrying the promotion entry: deterministic and
self-documenting, which a variable write is not.

**Existing behaviour intact:** 587 tests pass across all 24 halt, watchdog,
cancellation, deadman, reset and ownership suites on the deployed branch.

## Health and state after promotion

| check | value |
|---|---|
| watchdog sweep 02:44:29Z | `cancel_enabled true`, `open_orders 0`, `read_error null`, `still_open []`, `unverified []`, `not_ours []`, `overdue []`, `halted null` |
| **venue open orders** | **0** |
| executor heartbeat 02:44:38Z | `blocked true` / `NO_OWNER_ARM_TOKEN`, `owner_arm_token false`, `armed false`, `halt_latch CLEAR` |
| `live_orders` | **0** |
| ledger | **−8.82910000**, unchanged |

## MEASURED latency — and a correction to what I told you

**I previously said a variable-set HALT lands "within one ~20s sweep". That
was wrong, and requirement 5 was right to challenge it.**

`halt_now_reason()` reads `os.getenv`, and a running process's environment is
fixed. **Changing `L5_EMERGENCY_HALT_NOW` therefore requires a Railway
redeploy/restart before the running watchdog can see it.**

Measured components, not assumed:

| step | measurement | source |
|---|---|---|
| variable write → deployment SUCCESS | **~80s** | 81s here (02:41:09→02:42:30); 78s on the earlier watchdog write (02:06:20→02:07:38) |
| boot → first sweep evaluating the trigger | **≤20s** | `poll_s 20.0`, config emitted 02:42:28 |
| latch → executor acts on it | **8.45s** | watchdog wrote the record at 02:44:29.663832; the executor's heartbeat read that exact `record_t` at 02:44:38.116505 |
| **variable-trigger total** | **≈110s**, and contingent on a successful build | |

**A second correction.** I had reasoned that stopping the watchdog would halt
the executor quickly through a stale attestation. It would not:
`HaltGate` is constructed as `HaltGate(HttpHaltStore(url))` with no override,
so the bound is `DEFAULT_MAX_AGE_S = 900.0` — **15 minutes**. Stopping the
watchdog is a *slow* stop, not a fast one.

## The limitation, stated plainly, and the alternatives

**The variable trigger is a reliable durable latch but NOT the fastest
emergency stop**, because it cannot take effect without a redeploy. Three
mechanisms now exist and they are not interchangeable:

| mechanism | latency | needs | no redeploy? |
|---|---|---|---|
| **Stop the executor service** in the Railway dashboard | **immediate** — the process is gone, so no submission is possible at all | nothing | yes |
| **Signed marker** at `L5_EMERGENCY_HALT_URL` | **≈29s** (≤20s sweep + 8.45s measured) | your HMAC secret + any static file you host | **yes — polled in-process every sweep** |
| **`L5_EMERGENCY_HALT_NOW` variable** | **≈110s** | nothing but the dashboard | no — requires the redeploy |

**Smallest safe alternative that avoids the slow redeploy: the signed marker,
which is already deployed and needs only your two variables.** It is the only
one of the three that both latches durably and takes effect without a
redeploy — and the only one that works at all if Railway itself is
unavailable, which was its original reason for existing.

Stopping the executor service is faster still and needs nothing, but it does
not latch: resting orders are then cancelled by the independent watchdog on
its own schedule (`cancel_enabled true`, `max_order_age_s 300`), and nothing
prevents a later restart from arming again. Use it to stop bleeding; use a
latch to keep it stopped.

## Owner-facing emergency-stop procedure

**If you need it stopped RIGHT NOW (seconds):**
1. Railway dashboard → `l5-pmus-live` → **stop/remove the service**. No
   submission is possible once the process is gone.
2. Then latch it, so a restart cannot resume: set
   **`L5_EMERGENCY_HALT_NOW`** on `l5-halt-watchdog` to any reason string.
3. Confirm: `L5_WATCHDOG_SWEEP.halted` becomes non-null, and the executor's
   `submission_gate.halt_latch` reads `HALT` with a
   `halt_latch_telemetry.sha256` digest.

**If you have ~2 minutes (the ordinary case):**
1. Set **`L5_EMERGENCY_HALT_NOW`** on `l5-halt-watchdog` to any non-empty
   reason. Any value works; the value becomes the recorded reason.
2. The watchdog redeploys (~80s), asserts the durable latch on its first
   sweep, and the executor stops submitting and cancels **by exact venue
   order id** (`deadman()`, proven by re-reading `GET /v1/order/{id}`) within
   **8.45s** of the latch.

**To make it a ~29s stop with no redeploy**, set the marker's two variables
once, in advance — see `STAGE_B2_DEPLOYMENT_READINESS.md`.

**Clearing a latch is deliberately hard.** Clearing the variable does **not**
clear the halt — the latch is byte-identical afterwards, asserted by a test.
Only `scripts/l5_halt_reset.py` can, under four fail-closed gates: the phrase
`RESET THE LATCHED HALT`, a named human, the **sha256 of the latch you
reviewed**, a one-time id in an append-only audit, plus machine-verified zero
venue orders, zero venue position and a readable-fresh-empty registry, with
the audit fsynced before the clear.

**Before you ever fire a halt**, set `L5_HALT_RESET_AT_BOOT=YES` and
`L5_HALT_RESET_INSPECT=YES` once and read the read-only inspect record. It
yields the digest gate 2 will demand, so you have seen the way out before you
use the way in.

## What I did not do

I did not fire a test halt. Doing so would latch a HALT that only the
four-gate reset can clear, and gate 4 requires the operator's own attestation
— which is yours, not mine. So the latch-assert step above is the one figure
derived from the sweep cadence rather than observed; every other number is
measured.
