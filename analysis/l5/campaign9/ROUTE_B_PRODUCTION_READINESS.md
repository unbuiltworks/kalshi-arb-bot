
---

# DEPLOYED — verified 2026-10-29T17:36:48Z

| | |
|---|---|
| deployment | **`c208b3de-c79a-41a4-81c8-264aeceaec52`**, status **SUCCESS** |
| commit | **`c39183a3dd3c715d50af7e68788fc25fdc48d99a`** |
| service | `l5-pmus-live`, `online`, 1/1 replica, 0 warnings, 0 criticals |
| new container alive | `L5_LIVE_GATES` emitted 17:36:55Z from this deployment |
| `pendingWork` | `[]` |

## How it was deployed, and the two things that were NOT the cause

The GitHub webhook never advanced the service's pinned `commitSha`. Two
candidate explanations were investigated and both were wrong:

1. **The stale patch was not the cause.** `pendingWork` held
   `patch:8d2e08fe` (`EnvironmentPatch`, workflow `commitChanges`, staged
   17:30:19Z). `get-staged-changes` proved it **empty** — `staged: null`,
   `resources: []`, `sharedVariables: []`, `destructive: false`. Committing
   it with `accept-deploy` would have built **nothing**. I had suggested it
   might be the blocker; the owner pushed back and was right.
2. **`redeploy` would not have worked either.** Its contract is to reuse
   the existing BUILD, which would have re-shipped `202456d3`.

The actual fix was the supported existing-service mechanism: set
`config.source.commitSha` to the full SHA and deploy. **That one field was
the only change.** No GitHub reconnect, no new service, no variable touched,
no risk control altered, `l5-halt-watchdog` untouched.

## Safety controls re-verified on the NEW container

From `L5_LIVE_GATES` at 17:36:55Z, emitted by `c208b3de`:

| control | value |
|---|---|
| committed cash | **$12.00** |
| residual, contracts | **5** |
| residual, dollars | **$3.00** worst-case deficit |
| daily realized loss | **$5** |
| L5 cumulative loss | **$20** |
| account-wide cumulative | **$18.6325** (stricter; sits beside the $20) |
| cash policy | `NO_RECYCLING` |
| halt gate | `halt_gate_configured: true` |
| journal | **`journal_errors: 0`**, 32 entries, camp1 nonce intact |

Identical to the pre-deploy readings. Route B is present and **inert**:
`L5_BOOK_COMPLETION` is unset.

Still outstanding before any order: `max_total_exposure_usd` and
`max_live_orders` are only readable from the `L5_LIVE_ARMED` scope digest,
so they are checked at arming and arming halts unless they are exactly $20
and 1.

**NOT ARMED.** No campaign approval is installed; arming refuses
`NOT_ARMED` until the owner signs and installs a fresh eight-window
document.
