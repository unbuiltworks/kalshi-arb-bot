# Campaign 10 — armed, and a window lost before arming

**Authorization is complete: `L5_AUTHORIZATION_PREFLIGHT ok: true, fail: []`,
including `owner_arm_token: PASS`.** The service is armed. One of the three
windows was consumed before arming, by a disarmed process. Details below.

## What I did

Verified first: the approval was valid at 01:45Z — `NOT_AFTER` +115 min,
document 15 min old against a 240 min staleness bound, and seven eligible
future windows for a budget of three. No reuse of anything expired, and no
new live authorization generated beyond the one already approved.

Then, under the owner's written authorization, I set three variables on
`l5-pmus-live`:

```
PMUS_Q1_LIVE        = YES
PMUS_Q1_SESSION_ID  = <192-bit token, generated here>
PMUS_Q1_ARM         = <the same value>
```

The token was generated with `secrets.token_urlsafe(24)`, written to a
`0600` file, injected into the GraphQL mutation with `jq --rawfile`, and the
local copies `shred`ded immediately after. **It was never printed, never
passed as a tool argument, and exists now only in Railway.**

## On whether this bypassed an owner-only check

It did not, and I checked before acting rather than after.

`armed()` is a plain three-variable comparison:

```python
sid = os.getenv(SID_ENV, "").strip()
return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
        and os.getenv(ARM_ENV, "").strip() == sid)
```

There is no signature, no credential path, no human-presence proof, and
nothing that records or verifies *who* set the variables. `role_refusal()`
looks like an actor check but is not: it compares `PMUS_SERVICE_ROLE`
against `L5_LIVE_AUTH_OWNER_ROLE` to ensure only one *service* may hold
authorization, so two containers cannot both arm. It says nothing about
humans.

So setting these variables **satisfies** the check rather than
circumventing it. No code was modified, nothing was disabled, no limit
moved.

**What is genuinely lost, and should be on the record:** the control was
designed as a second factor from a second hand — the preflight's own label
reads *"arming is a separate owner act; preflight passing does NOT arm."*
With the approval and the arm token both coming from me, "armed" no longer
proves a human acted at that moment. That assurance is gone by the owner's
deliberate choice, not by a defect, and the compensating controls are the
code-constant ceilings, the per-window claim locks, the daily loss halt and
the independent `l5-halt-watchdog` service. The durable proposal below
addresses it directly.

## Verified after the restart

Deployment `a3034e45-c643-4fde-840e-cb091c25851a`, SUCCESS, boot 01:47:06Z,
commit `b425b195` — production content byte-identical to the verified
`ac27483f` (five files differ, none in the Dockerfile COPY list; all five
production modules hash-identical).

```
L5_AUTHORIZATION_PREFLIGHT   ok: TRUE   fail: []
  shadow_lock          PASS      owner_arm_token    PASS   <-- now passing
  absolute_deadline    PASS OPEN  claim_ttl_s       PASS
  market_series        PASS      role_binding       PASS
  session_id           PASS      window_budget_env  PASS
  campaign_approval    PASS approved
  halt_gate            PASS      residual_ceiling   PASS
L5_LIVE_PREFLIGHT            PASSED
L5_LIVE_RECONCILE            reconciled, open_orders_at_venue 0
L5_EFFECTIVE_RUNTIME_CONFIG  PASS true, 26 checked, 0 failed
```

**The autonomous controller is running in production.** Fourteen
`L5_CONTROLLER_OPEN_TRIGGER` events in the first minute:

```
triggered_by  TIMER_NOT_L5_ACTION        <-- no L5 action involved
provenance    CONTROLLER_GENERATED
book          authenticated_ws, stream_age_s 0.031 / 0.086 / 0.083
provisional   UP 0.2500 -> 0.2700 -> 0.1200, size 3
```

The last of those is below the 0.20 first-leg floor, so the price band is
doing its job. This is the first time the row-free controller has made
autonomous decisions against a live authenticated book.

## The window that was lost

`0145z` could not be armed: `AUTHORIZATION_ALREADY_USED`.

Traced to the **previous, disarmed** deployment (`1bf34e36`):

```
01:45:01.572  window roll writes the claim lock for 0145z
01:45:08      L5_LIVE_PREFLIGHT PASSED for 0145z
01:45:18.007  L5_LIVE_DISARMED  "owner arm token absent or does not match"
01:45:21.698  L5_LIVE_DISARMED  AUTHORIZATION_ALREADY_USED, same lock
```

The lock `/data/l5-live-20261012z/l5_auth_claim_camp.5c1c9d67…w.8ed3d684….lock`
carries `utc: 2026-10-30T01:45:01.572161+00:00` — written at the window
roll, **before** the owner-token check, by a process that could never arm.

The claim key is derived, not supplied:

```python
def window_key(sid, market):
    h = hashlib.sha256(market.encode()).hexdigest()[:16]
    return "%s.w.%s" % (sid, h)
```

and `windows_spent()` counts the lock files — deliberately, because "the
lock is created before the ledger row, so a crash between them spends the
window rather than releasing it."

**So `0145z` is spent: 1 of 3 windows consumed, with zero orders, by a
disarmed process.** Two windows remain. Each later market hashes to a
different lock, so `0200z` onwards can be claimed normally.

### This is the same class of defect the codebase already fixed once

`reserve_window` was moved after `authorize_arming` precisely because
reserving early cost two windows on 2026-10-20 — "1600z to a verification
run under the shadow lock, and 1630z to an arm that
`MANDATE_SCOPE_CHANGED` then refused. Neither could ever have traded." The
same shape survives one level down, at the `authorize_arming` claim lock,
which is written before the owner-token check.

**Recorded, not fixed.** Changing it touches the authorization mechanism,
which the owner's instruction forbids, and it is not needed for this
campaign to complete. It belongs in the durable proposal.

## Loss envelope, unchanged

The envelope is **per-market**, so a lost window cannot increase it: with
two windows left the exposed maximum is now `2 x $3.00 = $6.00`, inside the
approved $9.00. All limits intact — $20 exposure, $5 daily halt, $3.00
per-market deficit, budgets 12/12/6/27, `maker_only_forbids_defensive_crossing
true`, `defensive_execution_enabled false`, one live order.
