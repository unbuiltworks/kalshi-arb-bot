# Restart design for the continuous L5 runner

OWNER 5573574441 asked, after R4, for "the staged horizon reporting + probe-side
no-candidate alarm + restart design for the continuous runner". The first two
are staged on this branch (2f384c43 names a HORIZON exit in the log and report;
d284bb81 adds the `no_candidate` alarm). This note is the third: how the probe
should stop and start once a campaign is CONTINUOUS (R5F: no market count, only
the daily stop and the horizon), without loosening a gate, a limit, or the
"restart NEVER" rule.

## What exists today (facts from the code and the live record)

| mechanism | where | behaviour |
|---|---|---|
| horizon | `PMUS_SPRING_CAMPAIGN_SECONDS` (default 6 h; set to 604,800 s = 7 d on 5529b8bb at 16:52Z) | the campaign loop ends at `campaign_start + horizon`; the process exits 0 |
| restart policy | `deploy/pmus_spring_l5/railway.json` `restartPolicyType: NEVER` | a finished or crashed container is never restarted by the platform; only an owner redeploy (or a push to a watched path) starts a new one |
| stop signal | `SIGTERM -> KeyboardInterrupt` in the entry point | the session's deadman cancels the one outstanding order on the way out; `campaign_interrupted` is logged |
| durable state | `l5_campaign_state.json` in the state dir (campaign id, rung, filled markets, previous campaigns, sessions) | restored at boot (`campaign_state_restored`); an approval already consumed is never re-run |
| held position | resume path (`resume_start` per cycle) | a market the account holds is continued from the venue's own inventory, never re-opened; held to settlement |
| arm | `PMUS_SPRING_LIVE` + `PMUS_SPRING_SESSION_ID` + `PMUS_SPRING_ARM` (owner's dual key) | unchanged by any restart; the runner fails closed without it |

Live evidence: deployment dac5e439 exited at 14:48:37Z exactly at its 6-hour
horizon with nothing held (the R4 campaign had 2 of 3 markets settled), and the
gap until the owner's redeploy at 16:53Z was 2 h 5 min of no trading. That gap
is the problem this design removes for a continuous rung.

## The two failure modes a continuous runner must not have

1. **Horizon expiry while a market is held.** Today the loop ends and the
   process exits; the residual stays on the venue (correct: HOLD to
   settlement), the deadman cancels any open order (correct), but nothing
   continues the market's L5 decisions until an owner redeploy. For R5F, where
   a COMPLETE at L5's touch may rest for the model's own re-evaluation, an
   exit in the middle of a held market is a fidelity loss.
2. **Silent quiet time.** A horizon exit, a daily-stop halt, or a no-candidate
   stretch looks identical in the log unless it is named. 2f384c43 names the
   horizon; d284bb81 names the no-candidate case; the daily stop already logs
   `halt`.

## Design

### D1. Horizon = "drain", never "cut"

At `campaign_start + horizon` the runner stops SELECTING new markets and
enters `CAMPAIGN_DRAINING:HORIZON`: every held market keeps being resumed
(same resume path, same caps) until it is flat and venue-settled; only then
does the loop end, with `exit_reason.reason = "HORIZON_DRAINED"` and the
settled markets listed. A drain has its own bound (`PMUS_SPRING_DRAIN_MAX_S`,
default 12 h: longer than any sports market's remaining life) after which the
runner exits `HORIZON_DRAIN_TIMEOUT` with the held market named, so a hung
settlement cannot hide.

Money limits are untouched: no new first legs during the drain; the per-market
cap, action size, residual units, daily stop and deadman all apply as before.

### D2. Restart = owner redeploy, rehydrated, never automatic

`restartPolicyType: NEVER` stays. A restart is the owner's act (Railway
redeploy, or a push to a watched path that the owner has approved), and the
runner must make that act safe at any moment:

* boot rehydrates campaign id / rung / filled markets from `l5_campaign_state.json`
  and the held market from the venue (already the case);
* boot inside a drain (`draining_since` persisted) resumes the drain, not a new
  campaign;
* the continuation preflight (flat account, zero open orders, ledger reconciled,
  buying power >= rung worst case, deployment identity known) gates any NEW
  campaign exactly as it gates an approval today.

### D3. Horizon for a continuous rung

R5F's campaign is continuous, so the horizon is an OPERATIONAL bound (how long
the owner's arm is valid), not a strategy bound. Recommendation: keep
`PMUS_SPRING_CAMPAIGN_SECONDS = 604800` (7 days) and treat expiry as a
scheduled owner touchpoint: the runner drains (D1), posts its horizon report
(2f384c43), and the owner redeploys to continue. No automatic re-arm.

### D4. What is announced

Every transition is a named log row the check-in reads and reports:
`campaign_horizon_reached` (with `draining_held`), `campaign_drained`,
`no_candidate_alarm`, `halt` (daily stop), `campaign_interrupted` (SIGTERM).
The horizon report block (`exit_reason`) carries `resume` = the exact redeploy
step.

## What is implemented vs staged vs designed

| item | state |
|---|---|
| horizon exit named in log + report | staged, 2f384c43 (this branch) |
| no-candidate alarm | staged, d284bb81 (this branch) |
| SIGTERM deadman, durable campaign state, resume path, restart NEVER | live |
| D1 drain, D2 drain rehydration, D3 horizon choice, D4 `campaign_drained` | designed here; code is a watched-path change and is NOT written until the owner says which rung it ships with (it changes when the probe exits, not any limit) |

Nothing in this note changes a gate, a cap, the arm, or the frozen policy.
