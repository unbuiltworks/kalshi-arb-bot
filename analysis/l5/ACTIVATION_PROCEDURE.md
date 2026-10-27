# Activation procedure — four states, fail closed

Written after the 06:45–08:45Z campaign of 2026-10-27 failed to activate.
No strategy, financial limit or authorization contract changes here. This is
operational procedure only.

## 1. What went wrong

| time | event |
|---|---|
| 06:37Z | `set-variables` accepted the approval + `PMUS_Q1_LIVE=YES`; returned `skippedDeploys: false`, `staged: null` |
| 06:37–11:09Z | **no deployment row created**; the container from 04:52Z kept serving the OLD env (approval cleared, `PMUS_Q1_LIVE=NO`) |
| 06:45–08:45Z | the approved eight windows elapsed with the service structurally unable to arm |
| 11:09:47Z | a deployment finally began building, 4h32m after the write |

Result: **zero submissions, zero fills, zero exposure, zero realized P&L.**
Account unchanged at −$13.63250000. The outcome was safe, but by luck of two
expiries (`expires_utc` and `L5_LIVE_AUTH_NOT_AFTER`, both 10:06Z) rather than
by design.

**Root cause of the delay is not determinable from available evidence.** The
write was live, not staged; `pendingWork` is empty; and every other variable
write in this session deployed in 2–5 minutes, including the disarm at 11:11Z.
It presents as a Railway-side failure to enqueue that one deploy. The fix below
does not depend on knowing why, because it never infers runtime state from a
write acknowledgement.

## 2. The defect in the old procedure

It treated one fact — "the API accepted my variables" — as if it were four:

```
A  write acknowledged      set-variables returns
B  deployment created      a new deployment row exists
C  deployment SUCCESS      that row reaches status SUCCESS
D  runtime adoption        the RUNNING container reports the new values
```

Only **D** authorizes trading. A..C are necessary and individually
insufficient. On 2026-10-27, A held and B never did.

## 3. The procedure

The approval must not exist before the boundary preceding W1, because a valid
approval claims a window at every boundary whether or not the service can trade
(three windows were lost to this earlier the same day). That caps the usable
lead time: **mint after the preceding boundary, so there is strictly less than
15 minutes to reach state D.**

1. **Wait** for the boundary before W1 to pass.
2. **A** — one `set-variables` batch: approval (fresh nonce) + `PMUS_Q1_LIVE=YES`.
   Record the returned `skippedDeploys` and `staged`.
3. **B** — within 90 s, `list-deployments` must show a NEW deployment id created
   after the write. If not, call `redeploy` explicitly (it reuses the existing
   build, so the pinned `commitSha` is preserved). If still none: **ABORT**.
4. **C** — poll `list-deployments` until that id is SUCCESS, and `get-status`
   until `pendingWork` is empty. If not SUCCESS with ≥4 minutes left before W1:
   **ABORT**.
5. **D** — read the running container's heartbeat and require ALL of:
   - `approval_state: approved` and the document sha256 of the minted doc
   - the exact nonce in `campaign_id`
   - `expires_utc` equal to the minted expiry
   - `windows_spent: 0`, `max_windows: 8`
   - `max_cumulative_loss_usd: 18.6325`, `max_cash_deployed_usd: 12.00`
   - tickets `OPEN 0/20 COMPLETE 0/16 REPLACE 0/24`, submissions `0/40`
   - `halt_latch: CLEAR`, `preflight: PASSED`, `live_orders: 0`,
     `pending_terminal: []`, `quarantined: []`
   - `reconciled: true`, `ALERT: null`
   Any mismatch or unreadable field: **ABORT**.
6. **First order gate** — on the first arm, require
   `budget_release.released: true`. Anything else halts before submission.

### ABORT means

**Clear `L5_CAMPAIGN_APPROVAL` immediately and set `PMUS_Q1_LIVE=NO`.** This is
not optional: an approval left in place while a deployment is late will start
claiming windows the moment it lands, unsupervised. Then report and re-schedule.

## 4. Evidence the procedure works

Demonstrated on the disarming change of 2026-10-27, which submits no orders:

| state | evidence |
|---|---|
| A | `set-variables` → `{"variableNames":["L5_CAMPAIGN_APPROVAL","PMUS_Q1_LIVE"],"skippedDeploys":false,"staged":null}` |
| B | deployment `e3cbf8df-d387-415f-be80-e91601b2ec6e` created 11:11:23Z |
| C | `get-status` → that id `SUCCESS`; `pendingWork: []`; `stagedChanges: null` |
| D | heartbeat 11:14:30Z → `approval_present: false`, `approval_state: approval_absent`, `armed: false`, `owner_arm_token: false`, `halt_latch: CLEAR`, `live_orders: 0`, `all_time_realized_usd: -13.63250000` |

Four states, each confirmed from its own source. Turnaround A→D was 3m30s.

The same sequence on the 06:37 write would have stopped at **B** within 90
seconds and aborted, clearing the approval — instead of leaving a live
`PMUS_Q1_LIVE=YES` and an approval document pending for four and a half hours.

## 5. Unchanged

Eight-window ceiling, $12 cash envelope, $20 exposure, 3-contract residual cap,
0.96 pair ceiling, 0.20 first-leg floor, maker-only, one live order, recovery
cap 1, $5 daily, $5 campaign additional-loss, $18.6325 cumulative-loss ceiling,
tickets 20/16/24/40, pinned SHA `f6ad0f55022e98f5354234a5f1aaba0459084ed5`.
No new service. No change to the authorization model.
