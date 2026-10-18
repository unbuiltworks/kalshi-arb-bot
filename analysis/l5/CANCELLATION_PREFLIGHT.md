# Cancellation proof: full preflight. NOTHING SCHEDULED.

Read-only. No window scheduled, no variable written, no order. No new live
authorization is claimed by this document — the owner's instruction grants
none, and three of the items below are the owner's to supply.

State read at **2026-10-18T22:18:31Z** from deployment `b356e35a`
(SUCCESS 21:06:38Z). The service has NOT restarted since 21:06:37Z: the
repeated lock/gate lines at 22:00:03Z, 22:15:07Z and 22:16:18Z are the
periodic reconcile re-emission, and no `L5_LIVE_START` appears after 21:30Z.

## A. Verified GREEN — mine to check

| item | state | evidence |
|---|---|---|
| shadow lock | **locked: true** | `L5_LIVE_SHADOW_LOCK` 22:15:07Z, `blocked:["submit"]`, `cancel_still_permitted:true` |
| mechanism mode | **OFF** | no `L5_MECH_MODE` emitted since the lock was restored |
| durable claim ledger | **durable: true** | `auth_ledger` nearest_mount `/data` |
| holder identity | boot_id, deployment_id, pid, replica_id, service_role all true | `L5_LIVE_START` |
| cash cap | **$4.65** | `L5_LIVE_GATES` 22:16:18Z |
| per-market / total exposure | **$5 / $5** | `L5_LIVE_START` limits |
| concurrent markets / live orders | **1 / 1** | same |
| daily realized loss halt | **$5** | same, and `max_l5_daily_loss_usd` $5 |
| cumulative loss ceiling | **$20** | `max_l5_cumulative_loss_usd` |
| residual cap | **10 contracts** | `max_abs_residual` |
| cash policy | **NO_RECYCLING**, `max_windows` 20 | gates |
| halt gate configured | **true** | `halt_gate_configured` |
| watchdog | **live**, `cancel_enabled: true`, `read_error: null`, `halted: null` | `c1f6da55` sweeps every ~20s |
| open orders | **0** | watchdog venue read |
| HALT latch | **not latched** | `halted: null` |
| one-shot marker | **unspent** (`already_ran:false`, `marker:null`) | 21:01:09Z |
| submission claim | **unspent** — no order has ever been submitted | ledger |

## B. BLOCKERS — the owner's to supply. Each one alone stops the test.

| # | item | current | required |
|---|---|---|---|
| **B1** | `PMUS_Q1_SESSION_ID` | `l5-stageA-20261014-2215z-7f3c9a1e` — **SPENT** | a **fresh, never-used** session id |
| **B2** | `PMUS_Q1_ARM` | matches the SPENT id | must equal the NEW session id exactly (`Q1.armed()` requires `PMUS_Q1_LIVE=="YES"` AND non-empty session id AND `ARM == SESSION_ID`) |
| **B3** | `L5_LIVE_PROBE_MARKET` | `cpc-btc-updown-15m-2026-10-18-2100z` — **STALE**, that window closed 21:15Z | the slug of the chosen FUTURE window |
| **B4** | `L5_LIVE_AUTH_NOT_AFTER` | **OPEN**, expires **2026-10-18T23:59:00Z** → **~1h36m left** | must cover the chosen window; a window after 23:59Z needs a new deadline |
| **B5** | approval | none | explicit approval to flip `L5_CANCEL_MECHANISM=YES` / `L5_SHADOW_ONLY=0` |

**B1 is the exact refusal that killed the 21:00z attempt:**
`AUTHORIZATION_ALREADY_USED`, `boundary: l5_live_authorization.authorize_arming`,
with the prior claim recorded against `cpc-btc-updown-15m-2026-10-14-2230z`
on 2026-10-14T22:32:23Z. One authorization trades once; the durable claim
lock survives restarts, which is the behaviour we want, not a bug to clear.
The fresh `L5_EXPERIMENT_ID` already set does NOT satisfy it — arming keys
on the session id, not the experiment.

## C. Timing constraint on whichever window is chosen

The mechanism's own gates, unchanged:

- `MIN_WINDOW_REMAINING_S = 450` → the order must be placed within the
  first **7.5 minutes** of a 15-minute window;
- watchdog `max_order_age_s = 300` → it cancels once the order has rested
  **5 minutes**;
- `SELF_CANCEL_AFTER_S = 390` → the executor's own backstop fires at
  **6.5 minutes**, and a self-cancel is explicitly NOT counted as proof.

So the watchdog has a ~90-second window in which to act first. Activation
must therefore land, and the transport must arm, **before window_open +
450s** — and a Railway variable write takes ~100s to build and boot, with
a worker-restart delay already observed at 21 minutes once today. That is
why the variables should be set BEFORE the window opens, not during it.

## D. What "proven" requires, unchanged

1. an actual venue order id, persisted with ownership evidence BEFORE the
   watchdog is relied on;
2. cancellation **initiated by the independent watchdog**, not executor
   self-cancel and not account-wide;
3. the venue's own re-read showing a **terminal status** for that id;
4. shadow lock restored and HALT state reported afterwards.

Nothing short of all four is cancellation proven. A self-cancel backstop
firing is reported as `SELF_FALLBACK` and is a FAILED proof, not a pass.

## E. Recommended sequence — on approval only

1. owner sets B1+B2 (fresh session id and matching arm token), B3 (the
   chosen window's slug) and, if needed, B4 — ideally all in one write at
   least 5 minutes before the window opens;
2. I verify the resulting deployment reaches SUCCESS and read the boot line
   back: lock still on, `auth_not_after` OPEN, limits unchanged, and the
   `L5_MECH_MODE` market matching the authorized slug exactly;
3. at window open I flip the mechanism on and the lock off, verify the
   deployment and the effective market, and confirm the transport arms;
4. capture the four items in D, then restore the lock immediately.

I am scheduling nothing and writing no variable until B1–B5 are supplied.
