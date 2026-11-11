# Disarmed validation deployment — `5c09b52de` / deployment `804e314d`

Owner-approved 2026-11-09. **Disarmed. Nothing armed, no order, no campaign
approval, no limit changed, the operator halt not cleared.**

## What was deployed

| | |
|---|---|
| commit | **`5c09b52de692804439185a669e3556a2d55b9e24`** |
| deployment | `804e314d-e128-4e7d-bc85-1fe0cef824cf` **SUCCESS** 2026-11-09T14:53:07Z |
| previous | `41defbb9` / `68553c7cb` (four commits behind) |
| branch | `integration/l5-safe-release-candidate-20261109` |
| parity | 20 failed / 18,975 passed / 28 skipped / 1 xfailed / 14 errors; **34 IDs, `diff` clean** |

## How it was deployed, and why that mattered

`railway.json` sets `watchPatterns: ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, so
ordinary pushes cannot restart a process that may hold a resting order. That was
verified rather than assumed: the candidate was pushed and the deployment did
not move. A **variable change**, by contrast, rebuilds from the **branch tip** --
established from the 07:52Z deployment, which ran a commit the watch pattern
would not have deployed.

So the branch tip was pinned at exactly the parity SHA, and one variable write
(`L5_SHADOW_ONLY` -> `1`) produced one deployment carrying both the verified
candidate and the restored lock. Ordering was deliberate: the trigger route
would have deployed with the lock still open.

The change was first STAGED (patch `9891ae92`) and reviewed -- one resource, one
field, `destructive: false` -- before being applied.

## Effective submission gate in the booted container, 14:54:45Z

    blocked          True
    reason           OPERATOR_HALT
    operator_halt    True          <- NEW; absent on 68553c7cb
    shadow_lock      True          <- restored
    halt_latch       CLEAR
    owner_arm_token  True
    armed            False

`reason` is `OPERATOR_HALT` rather than `SHADOW_ONLY_LOCK` because the operator
halt now takes first precedence. Both are engaged; either alone blocks.

The authorization preflight is market-specific and now names the halt:
`fail: ['shadow_lock', 'local_operator_halt', 'absolute_deadline',
'campaign_approval']`, `evaluated_market: cpc-btc-updown-15m-2026-11-09-1445z`.
On `68553c7cb` the halt could not appear in that list at all, which is what
lost the 08:30Z window.

## Health

| | |
|---|---|
| watchdog | `PRESENT`, `halt=false`, record 14:54:29Z — 16 s before the heartbeat |
| orders | 0 live, 0 parked, `pending_terminal []`, `quarantined []` |
| reconciliation | `unresolved_accounting 0`, `blocks_live_authorization false` |
| recorder | `readable true`, 384,296 events, 18,664 orders, **0 unreadable**, all replayed from disk |
| exposure | $0 of $1.00 |
| daily | $0 of $5, `would_halt false` |
| taker | `L5_TS_COMPLETION_CROSS=NO`, `taker_policy_enabled false`, `taker_crossing_reachable false` |

## `L5_EFFECTIVE_RUNTIME_CONFIG` reads `PASS: false`, and that is CORRECT

Eight rows fail, every one with `required: NO_APPROVAL_IN_FORCE`: the four
submission budgets, both aggregate-exposure rows, the daily loss halt and
`max_windows_authorization`. Those rows are graded against **the owner's
campaign approval document**, and no document is in force -- the 0830z approval
expired at 09:00Z. Proven directly: `ApprovalGate.state()` on the installed
bytes returns `approved: False`, `scope: None`, so `approved_scope()` is `None`.

The module says so itself: *"Sentinel for a row whose authority is the owner's
approval document when no document is in force. It is NOT a value and never
compares equal to one, so such a row FAILS -- which is the whole point: an
unverifiable configuration is not an approved configuration."*

Every **effective** value is unchanged and correct: budgets 140/300/60/500,
exposure $1.00, in-force $1.00, daily halt $5, one window. This is not a
regression and not caused by the candidate; it is the fail-closed reading with
no approval in force, and it resolves when a fresh single-window approval is
installed.

## Still required, each needing separate owner approval

1. Reset `L5_LIVE_HALT`. All eight `HALT_RESET_PRECONDITIONS` hold as of
   14:56:39Z, and provenance is established: the owner asserted it on
   2026-11-06 between 13:19Z and 15:46Z as the standing disarmed posture, on an
   explicit recommendation recorded in `TWO_WORKSTREAM_GONOGO.md` and
   `INVENTORY_VERDICT.md`. Not an incident, not a loss, not automated --
   nothing in `src/` or `scripts/` can write it.
2. Publish a fresh single-window `L5_CAMPAIGN_APPROVAL`. Owner-only by code
   invariant: `EnvApprovalStore.put` raises "the executor may not write its own
   approval".
3. Clear `L5_SHADOW_ONLY` for that window, then arm.

**A variable write rebuilds from the branch tip.** Any write made after a later
push deploys that tip, not `5c09b52de`. Check the tip before writing one.
