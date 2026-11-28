# Disarmed deployment of the residual-cap remediation — evidence

**Deployed. Disarmed. Not rearmed. No campaign initiated.**

## What is running

| | |
|---|---|
| deployment | **`0072e385-1c07-4bde-b19e-9759f7d3cc42`**, SUCCESS, 2026-11-01T01:29:42Z |
| **running commit** | **`00a78b503beb42ee513c225581c1e964dc0092b4`** |
| approved commit | `cbcfb118cc43dff036a0aecfcd06796c4343a9f2` |
| engine identity | **`src/`, `deploy/` and `tests/` byte-identical** between the two (`src` tree `e22e135f…`); the sole difference is one added markdown file |
| previous deployment, kept | `2582730e-9b34-401c-b729-e3011f31a2af` on `2cf7ad65`, `canRollback: true`, `canRedeploy: true` |

**Two deployment facts I will not paper over.**

1. **The first attempt was SKIPPED, not deployed.** A `connect-service-source`
   pin change alone does not build, because the service's build
   `watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]` and a commit
   that does not touch that file is skipped. Deployment `0bb029af` records
   `status: SKIPPED`. Had I reported success at that point it would have been
   wrong: the service was still running `2cf7ad65`. The deploy was then
   triggered the way every successful deploy in this programme has been — an
   inert variable write, here `L5_REMEDIATION_SHA`.
2. **The running commit is branch HEAD, not the pinned SHA.** The service
   config still reads `commitSha: cbcfb118…`, but the variable-triggered
   redeploy built `00a78b50` (branch HEAD). The engine is identical, so
   nothing functional differs — but the config and the running deployment
   disagree, which matters for reproducibility. Pinning alone does not
   survive a variable-write trigger on this service.

## Disarmed state, after the restart

`L5_LIVE_START` 01:29:52Z and heartbeat 01:32:01Z:

| check | value |
|---|---|
| `owner_arm_token` | **false** |
| `armed` | **false** |
| `submission_gate.blocked` | **true**, `reason NO_OWNER_ARM_TOKEN` |
| `halt_latch` | CLEAR, `halt_why` null |
| `approval_state` | `approval_stale` |
| `auth_not_after` | **EXPIRED** (`expires_in_s -37912.8`) |
| `live_orders` | **0** |
| `unresolved_accounting` | **`{count: 0, records: []}`** |
| `all_time_realized_usd` | **−8.82910000**, unchanged |
| `available_cash` | $343.97825 = $342.56275 + the $1.4155 Stage B2 realized, exact |
| limits | `max_usd_per_market 20`, `max_total_exposure_usd 20`, `max_live_orders 1`, `max_concurrent_markets 1`, `daily_realized_loss_halt_usd 5`, `max_abs_residual 10` — **unchanged** |

**Three independent refusals** now stand between this process and the venue:
no owner arm token, a stale campaign approval, and an expired absolute
deadline.

`L5_SHADOW_ONLY` is currently `0` — released during the campaign and not
re-engaged. I did not change it: it is not a lock I would be removing, and
re-engaging it would mean another deploy nobody asked for. Say the word and
I will set it back to `1` for a fourth, structural refusal.

## Venue obligations

Independent `l5-halt-watchdog`, sweep 01:32:55Z: **`open_orders 0`**,
`still_open []`, `unverified []`, `not_ours []`, `overdue []`,
`cancelled []`, `cancel_enabled true`, `read_error null`, `halted null`.
Venue open orders and unresolved obligations are **zero**, confirmed by a
process independent of the executor.

## Both gates on the shared definition

Verified in the deployed tree, which is byte-identical to the tested one:

```
src/l5_live_execution.py:487   def residual_cap_decision(...)
src/l5_live_execution.py:6364      rc = residual_cap_decision(   <- final_residual_guard
src/l5_live_execution.py:7533     _rc = residual_cap_decision(   <- handle (primary gate)
```

**What I can and cannot evidence here.** Code identity and 201 passing
tests across five cap suites are the proof. Production telemetry is *not*:
with `owner_arm_token false`, arming is refused before any decision reaches
`handle`, so the gate is never exercised and `FILL_CONTINGENT_WORST_CASE`
correctly appears nowhere in the logs. Searching for it returned zero
records, which is the expected result and not a confirmation.

## Diagnostic

`L5_TAPE_RECOVERY_COID` empty. **Zero `L5_TAPE_RECOVERY` records** on this
deployment. No recurring tape scans. The latch is additionally in the code
now, so even if the variable were set the tape would be read once per
process rather than every window roll.

## No unexpected orders

Zero submissions, zero fills, zero cancels, zero rejects since the deploy.
The realized ledger is unchanged at −8.82910000 and cash moved only by the
already-settled Stage B2 result.

## Rollback

One call, no build: `redeploy(deploymentId=2582730e-9b34-401c-b729-e3011f31a2af)`
reuses the previous image exactly. Or pin to `2cf7ad65` and trigger with a
variable write — note the trigger requirement above, or the pin will be
SKIPPED. Earlier known-good: `dc7d383e`, `4d73b5b5`.

## HALT readiness — reset procedure VERIFIED in code

`scripts/l5_halt_reset.py` **is COPY'd into the deployed watchdog image**
(Dockerfile line 98), as is `l5_emergency_halt_marker.py`. The watchdog
imports the reset module at boot behind `L5_HALT_RESET_AT_BOOT`, so no new
entrypoint and no new service is needed.

**Four fail-closed gates, any one unmet writes nothing:**

1. **Intent** — `L5_HALT_RESET_CONFIRM` must equal exactly
   **`RESET THE LATCHED HALT`**, and `L5_HALT_RESET_WHO` must name a human.
2. **The latch you reviewed** — `L5_HALT_RESET_EXPECT` must be the sha256 of
   the current `halt.json` bytes. If the watchdog re-latched for a new
   reason between review and reset, the digest moved and it refuses. You
   cannot clear a halt you have not read.
3. **One time** — `L5_HALT_RESET_ID` is recorded in an append-only audit log
   and refused if already present, so a leftover admin setting clears
   nothing on the next restart.
4. **The condition is actually gone**, verified independently of the
   executor: the venue reports zero open orders; the venue reports no
   non-zero position; the ownership registry is readable, **fresh** and
   empty (readable-but-stale is refused, because a report that stopped
   arriving is the wedged signal); and the operator attests watchdog health,
   recorded verbatim.

The audit entry is appended and fsynced **before** the latch is cleared, so
a crash between the two leaves the halt latched and a record that a reset
was attempted — the safe asymmetry.

**And there is a read-only INSPECT mode**, which is how the digest for gate 2
is obtained without stopping the watchdog: `L5_HALT_RESET_INSPECT=YES` makes
`main()` print `L5_HALT_RESET_INSPECT {…}` and return without writing
anything.

## Next specific owner action

**One action, in the Railway dashboard, on `l5-halt-watchdog` only.** Three
variables; no new service; no secret in chat.

| variable | value |
|---|---|
| `L5_EMERGENCY_HALT_SECRET` | `openssl rand -hex 32`, generated by you, **marked SEALED** so Railway never reads it back to anyone, me included |
| `L5_EMERGENCY_HALT_URL` | any static file you control — a Gist raw URL is enough; integrity is the HMAC, not the transport |
| `L5_EMERGENCY_HALT_SERVICE` | `l5-pmus-live` |

Then, to rehearse safely while disarmed: set `L5_HALT_RESET_AT_BOOT=YES` and
`L5_HALT_RESET_INSPECT=YES` first and read the inspect record, so you hold
the digest and have seen the reset path *before* you ever latch a halt.
After that, publish a signed marker and watch `halted` go non-null within
one ~20s sweep and the executor's `halt_latch` flip `CLEAR → HALT` with its
sha256 acknowledgement.

I did not create the marker host and did not generate the secret: both are
yours by design, and doing either would defeat the control.

No profitability experiment was run and none is proposed.
