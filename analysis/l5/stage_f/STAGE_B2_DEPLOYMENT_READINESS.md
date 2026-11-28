# Stage B2 remediation — deployment readiness, rollback, and the HALT path

**NOT DEPLOYED. NOT REARMED.** Blocked on the HALT activation path below.

## The candidate

| | |
|---|---|
| **SHA** | **`cbcfb118cc43dff036a0aecfcd06796c4343a9f2`** |
| branch | `claude/compassionate-allen-wic64u` |
| contains | both residual gates on one worst-case definition; the final pre-wire guard; `outstanding_on_side`; `RESIDUAL_CAP`/`INVENTORY_CLASS`/`FINAL_RESIDUAL_GUARD` emitted; order-stage counters separated; the one-shot tape diagnostic (default OFF, latched) |
| parity | **54 failed / 23 errors — identical to baseline, file for file**; 14803 passed against 14716 (+87) |
| cap suites | **201 passing** across five files |

## What is running right now

| | |
|---|---|
| deployment | `2582730e-9b34-401c-b729-e3011f31a2af`, created 18:40:22Z |
| commit | **`2cf7ad65b3bb0068441d8c5c91845ab12dee5174`** (`recovery/tape-1415z`) |
| contents | pre-remediation engine + the tape diagnostic only |
| diagnostic | **OFF** — `L5_TAPE_RECOVERY_COID` cleared; zero `L5_TAPE_RECOVERY` records since 18:42 |
| arm state | `owner_arm_token false`, `armed false`, `blocked true` (`NO_OWNER_ARM_TOKEN`), `live_orders 0` |
| source | **pinned to a commit**, so branch pushes do not auto-deploy |

The pin is deliberate: during an incident, no push can deploy by accident.

## Deployment procedure, when HALT is resolved

1. Confirm `owner_arm_token false` and `live_orders 0`.
2. Point the service at the remediation SHA — the service is already pinned,
   so this is a pin change and nothing else:
   `connect-service-source(repo=unbuiltworks/kalshi-arb-bot,
   branch=claude/compassionate-allen-wic64u, commitSha=cbcfb118)`.
3. Confirm the new deployment reports `L5_LIVE_START` with
   `owner_arm_token: false`, and that `submission_gate.blocked` is `true`
   with `reason: NO_OWNER_ARM_TOKEN`.
4. Confirm `limits` still read `max_total_exposure_usd 20`,
   `max_usd_per_market 20`, `max_live_orders 1`,
   `daily_realized_loss_halt_usd 5`, `max_abs_residual 10`.
5. Confirm `L5_TAPE_RECOVERY_COID` is still empty.
6. **Do not arm.** Arming is a separate owner action and needs a fresh
   campaign authorization.

## Rollback

**One call, no build:** `connect-service-source(... commitSha=2cf7ad65)` —
the currently-running recovery commit, whose image is already built. Or
`redeploy(deploymentId=2582730e-9b34-401c-b729-e3011f31a2af)` to reuse that
exact build.

Earlier known-good points, in order of recency:
`dc7d383e` (Stage B2 final, pre-remediation), `4d73b5b5` (the commit that
ran windows 1-6 of the campaign).

Rollback is safe at any time because the engine is disarmed: no rollback can
place an order, and cancellation is never gated by any of this.

**Nothing else was touched.** `l5-halt-watchdog`, `l5-book-shadow`, the
collectors and every unrelated service are as they were. No service,
database or volume was created, and the decision tape is byte-unchanged.

## The remaining blocker: HALT activation

`l5_emergency_halt_marker` is built, wired into the existing watchdog sweep
(`scripts/l5_halt_watchdog.py:169-185`), copied into the watchdog image, and
**inert** — `configured()` requires BOTH `L5_EMERGENCY_HALT_URL` and
`L5_EMERGENCY_HALT_SECRET`, and neither is set on `l5-halt-watchdog`.

### The simplest secure way to configure it — no secret in chat, no new service

The secret never needs to leave your hands. Railway variables are set by you
in the dashboard, and the marker URL can be any static file you control.

1. **Generate the secret yourself**, locally, and never paste it anywhere
   but the Railway variable field:
   `openssl rand -hex 32`
2. **In the Railway dashboard**, on `l5-halt-watchdog` only, set:
   - `L5_EMERGENCY_HALT_SECRET` = that value. **Mark it sealed** — Railway
     then never reads it back, to me or to anyone.
   - `L5_EMERGENCY_HALT_URL` = a URL you control that serves one small JSON
     file. A GitHub Gist raw URL, an S3 object, or any static host is fine;
     it needs no authentication, because integrity comes from the HMAC and
     not from the transport.
   - `L5_EMERGENCY_HALT_SERVICE` = `l5-pmus-live`.
3. **Publish an empty or absent marker** to begin with. An unfetchable or
   non-HALT marker returns `MARKER_UNREACHABLE` / `MARKER_COMMAND_NOT_HALT`
   and changes nothing.

Why this is safe to hold: the marker can only ever **stop** trading.
`HaltAsserter` has no clear, no resume and no delete by construction — a
test asserts those methods do not exist — so even a leaked secret or a
forged marker achieves nothing worse than halting us, and `COMMAND` parses
`HALT` and nothing else.

### To fire it

Publish this JSON at that URL, with `hmac` computed over the five signed
fields (`command`, `issued_utc`, `nonce`, `service`, `reason`) using your
secret. `python3 -c "from src import l5_emergency_halt_marker as E;
print(E.sign(doc, SECRET))"` computes it locally:

```json
{"command": "HALT", "service": "l5-pmus-live",
 "issued_utc": "<now, ISO-8601 UTC>", "nonce": "<any unused string>",
 "reason": "<why>", "hmac": "<hex>"}
```

### To exercise it without trading — the test you asked for

Do this **while disarmed**, which is now:

1. Publish a valid marker.
2. Watch `L5_WATCHDOG_SWEEP` for `halted` becoming non-null, within one
   sweep (~20s).
3. Watch the executor's `submission_gate` flip `halt_latch` from `CLEAR` to
   `HALT`, with a `halt_latch_telemetry.sha256` digest as the
   acknowledgement, on its next tick.
4. Confirm `deadman()` cancellation ran — `L5_WATCHDOG_SWEEP.cancelled` and
   the executor's own cancel path. With zero open orders there is nothing to
   cancel, which is the safe way to rehearse it.
5. **Measured expectation:** under a minute end to end. The latch is read
   over HTTP on the executor's own tick — the 16:20:50 heartbeat carried a
   latch `record_t` of 16:20:39, eleven seconds old — against roughly six
   minutes for the Railway redeploy that was the only lever on 2026-10-31.
6. **To clear it afterwards:** `HaltAsserter` cannot, by design. Clearing is
   the existing owner reset path — `L5_HALT_RESET_*` on the watchdog, with
   `L5_HALT_RESET_EXPECT` set to the digest the heartbeat published. Note
   this before firing it, so the rehearsal does not leave a latch you did
   not plan to clear.

Alternative if you would rather not host a file at all: the same variables
accept any URL, so a Railway-internal static endpoint or a one-line Gist
both work. I have **not** created either — that is yours to choose, and
creating it would be the new infrastructure you ruled out.

## Status

| requirement | state |
|---|---|
| both gates on one conservative definition | **done**, `cbcfb118` |
| opposite-side resting never a hedge; same-side counts | **done**, 32 tests |
| regressions: 14:21 state, partials, simultaneous, cancels, stale, flat | **done**, 201 passing |
| one-shot latch verified, diagnostic default off, no recurring scans | **done**, latch + 3 tests; verified silent in production |
| full baseline parity | **done**, exact |
| unrelated services preserved | **done**, nothing else touched |
| deployment prepared with SHA and rollback | **done**, above |
| HALT activation | **BLOCKED on your two variables** |

No profitability experiment was run and none is proposed.
