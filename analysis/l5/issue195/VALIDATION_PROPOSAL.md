# Live validation proposal — PR #197 at `3f9ca7458`

**Nothing in this document has been done.** It is a proposal. No deploy, no
arming, no variable write, no campaign mint. Every step below is an owner
action and they are deliberately separable.

---

## The deployment itself needs two owner actions, and they are separate on purpose

The service's actual config, read from Railway:

```
source.branch     claude/compassionate-allen-wic64u      <- the PR BASE
build.watchPatterns  ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]
dockerfilePath    deploy/l5_pmus_live/Dockerfile
startCommand      python3 -m src.l5_pmus_live_runner
volumeMounts      /data
```

So to run `3f9ca7458` on `l5-pmus-live`:

1. **Point the source at the code.** Either re-pin `source.branch` to
   `fix/l5-issue195-rollover-and-real-completion`, or merge #197 into
   `claude/compassionate-allen-wic64u`.
2. **Touch `deploy/l5_pmus_live/DEPLOY_TRIGGER`.** The watchPattern means no
   build happens without it — this is what keeps ordinary branch pushes away
   from the money-capable service, and it is why PR #196's push correctly
   `SKIPPED`.

**This PR deliberately does not touch `DEPLOY_TRIGGER`.** If it did, merging
#197 would *itself* deploy. Merge and deploy stay two decisions.

### What a deploy does and does not change

| | |
|---|---|
| container | replaced; a fresh one starts `armed = false` |
| `/data` volume | **preserved** — the ledger, the cursor, `defensive_spent_usd`, the claim ledger, settlement history |
| `L5_LIVE_HALT` | unchanged (currently **empty**, i.e. not asserted) |
| `approval_state` | unchanged (`approval_stale`, `windows_remaining: null`) |
| the three new switches | **present but not `YES`** on `l5-pmus-live`, so OFF — corrected below, this was wrong when written |
| rollback | `canRedeploy` is true on the prior deployment |

### CORRECTION, measured on the running Phase A deployment

This document originally said the three new switches were *absent from the
service*. That was wrong, and the correct reading is the stronger one.

`list-variables` on `l5-pmus-live` shows **`L5_TAKER_COMPLETION_POLICY` and
`L5_TS_COMPLETION_CROSS` are both SET**. Values are redacted to a connected
app, so the switch state was read from the process's own output instead —
deployment `a342fccc`, 2026-11-06T15:38-15:39Z, `L5_TS_COMPLETION`:

```
"authorized_to_cross": false            <- L5_TS_COMPLETION_CROSS is not YES
"taker_policy": {"reason": "POLICY_DISABLED", "default": "OFF"}
                                        <- L5_TAKER_COMPLETION_POLICY is not YES
"armed": false
"reason": "NO_INVENTORY_TO_COMPLETE"    <- the named refusal, not WOULD_SUBMIT
"cash_headroom": "12", "worst_case_ceiling": "1.50"
```

So both switches exist with non-`YES` values and both evaluate OFF. What makes
that safe is `_flag`, which is an exact `== "YES"` string comparison and not a
truthy env read, so a stray `0`, `false`, `off` or empty value cannot enable
live crossing. A set-but-not-YES variable is the case that would have defeated
a truthy check, and it is the case that is actually on the service.

The remaining three — `L5_TAKER_LOSS_REDUCTION`,
`L5_TAKER_MIN_WORST_IMPROVEMENT_USD` and `L5_MAX_COMPLETION_EV_GIVEUP_USD` —
are genuinely absent from the variable list. With the floor unset the rescue
path refuses with `RESCUE_IMPROVEMENT_FLOOR_UNSET`, so Phase B needs all three
written, not just the two that exist flipped to `YES`.

Phase B therefore requires **five** variable writes, not three.

---

## Phase A — the stall fix, at zero capital risk

**Deploy `3f9ca7458` disarmed. Change nothing else.** No campaign, no arm
token change, no new variable. This verifies the single highest-value fix with
no money at stake, because the stall is a *latency* defect and is observable
while disarmed.

Run for **≥ 8 consecutive windows (≈2 hours)**.

### Acceptance — all five, from the service's own logs

| # | criterion | where |
|---|---|---|
| A1 | `roll` stage **< 2 s** on every window (was 92.9–93.9 s) | `L5_LIVE_TICK_STAGES.stages_s.roll` |
| A2 | **zero** `L5_LIVE_ROLL_OVER_BUDGET` lines | absence |
| A3 | `L5_COST_BASIS_AUDIT` emits **exactly once** per process, not 96×/day, and still reports `writes: NONE` with `activity_walk.complete: true` | `L5_COST_BASIS_AUDIT.deferred_job` |
| A4 | settlement and reconciliation cadence unchanged: `L5_LIVE_WINDOW`, `L5_LIVE_SETTLED`, `L5_LIVE_RECONCILE` still once per window | log counts vs the prior deployment |
| A5 | no traceback, no `ModuleNotFoundError`, no `CRITICAL`; `armed false`, `submission_gate blocked` throughout | `L5_LIVE_HB` |

**Abort condition:** any of A1–A5 fails ⇒ roll back to the prior deployment
(`canRedeploy`) and report. No capital is exposed either way.

**Phase A alone also answers the fifth validation target — rollover latency —
and it needs no authorization beyond the deploy.**

---

## Phase B — capped live validation, only after Phase A passes

### The size is set by the ledger, not by preference

```
max_cumulative_loss_usd  18.6325
realized_today           14.7495
headroom                  3.8830

1 window  worst case 1.50  FITS, margin 2.3830
2 windows worst case 3.00  FITS, margin 0.8830
3 windows worst case 4.50  DOES NOT FIT, margin -0.6170
```

**Two windows is the maximum the cumulative ceiling permits.** A three-window
campaign would be refused by the campaign layer regardless of what anyone
authorizes, so the proposal is two.

### Proposed configuration

| setting | value | why |
|---|---|---|
| windows | **2** | the headroom allows no more |
| contracts per leg | **1** | smallest the grid expresses; the deficit in both replayed windows was 1 |
| `L5_TS_MAX_PAIR_COST` | unchanged **0.96** | entry ceiling untouched |
| `L5_TAKER_COMPLETION_POLICY` | `YES` | the bounded route exists |
| `L5_TS_COMPLETION_CROSS` | `YES` | live taker execution |
| `L5_TAKER_LOSS_REDUCTION` | `YES` | without it the replay shows both rescues still refused |
| `L5_TAKER_MIN_WORST_IMPROVEMENT_USD` | **`0.25`** | see FINDINGS.md: the floor must be ≤ 0.35 or 0315z is lost; 0.25 captures both real windows at their best moment and cuts the marginal tail |
| `L5_MAX_COMPLETION_EV_GIVEUP_USD` | **`0.05`** | measured give-up was 0.0138–0.04; a 0.02 cap loses 0315z for the sake of a cent |
| `L5_TAKER_MAX_OUTLAY_USD` | unchanged **1.50** | per cross |
| `L5_TAKER_WINDOW_OUTLAY_USD` | unchanged **3.00** | per window |
| `L5_COMPLETION_REQUOTE` | already enabled | validation target 4 |
| `L5_LIVE_HALT` | **assert it before Phase B is minted**, clear it as the last step | it is currently empty |

### Maximum exposure, every layer

| bound | value |
|---|---|
| worst case, 2 windows | **$3.00** (margin $0.88 under the cumulative ceiling) |
| per-window worst-case deficit | $1.50 (`L5_MAX_WORST_CASE_DEFICIT_USD`) |
| cash deployed | $12.00 ceiling (`L5_MAX_CASH_DEPLOYED_USD`) |
| daily realized-loss halt | $5.00 |
| completion crosses, **lifetime** | **$2.00** total (`DEFENSIVE_LOSS_BUDGET_USD`, monotone, never reset, survives restart) — affords **3–5 attempts ever** |
| completion cross, per attempt | $1.50 |
| unmatched contracts | 5 |
| live orders | `L5_MAX_LIVE_ORDERS` |

### Acceptance — one criterion per validation target

| # | target | criterion |
|---|---|---|
| B1 | **simultaneous quoting** | both legs resting concurrently in ≥ 1 window: 2 live orders at the venue, both venue-confirmed working |
| B2 | **maker fills** | ≥ 1 venue-confirmed maker fill with an **attested** cost basis (`fill_count > 0`, no `$1/share` placeholder) |
| B3 | **second-leg completion** | ≥ 1 `L5_TS_COMPLETION_RESULT` with `outcome.submitted: true` **or** a *named* refusal — and **zero** occurrences of `WOULD_SUBMIT` anywhere in the logs |
| B4 | **repricing** | ≥ 1 `completion_requote` cancel-and-replace with the cancellation verified by venue re-read of the **exact** order id |
| B5 | **rollover latency** | `roll < 2 s` on both windows |
| B6 | *(safety, not a target)* | every limit held; `defensive_spent_usd ≤ 2.00`; settled P&L reconciles exactly against `realized_today`; no unmatched contract carried to settlement **without** a named refusal in the tape |

**B3 is the point of the whole exercise.** A refusal with a name is a PASS —
the defect was a decision that became neither an order nor an explanation.

### Abort conditions

Any of: a second crossing order in one market in one window · an unmatched
contract carried with no named reason · `defensive_spent_usd > 2.00` ·
`orders_uncertain` set and not cleared by reconciliation · any `roll > 10 s` ·
any traceback on the money path. On abort: assert `L5_LIVE_HALT`, let the
watchdog and deadman cancel by exact id, reconcile, report. Do not re-arm.

---

## Infrastructure cost

**Zero incremental.** No new service, no new volume, no new workflow, no new
cron. `l5-pmus-live` already runs continuously on an existing volume; a deploy
replaces its container. The only new cost is build minutes for one Docker
build. The 5 `COPY` lines added to two existing Dockerfiles add no layers of
consequence.

The one CI change (`fetch-depth: 0`) makes the checkout of
`l5-canonical-ci.yml` fetch full history — a few seconds and no dollars, on a
workflow that runs only on dispatch and two branches that are not this one.

---

## What is explicitly NOT proposed

* Re-running the unchanged losing strategy. The two windows exist to exercise
  the **completion route**, which did not exist in the campaign that lost
  −$0.97. If the completion route cannot be exercised — no first-leg fill, no
  unmatched inventory — the validation produces **no** result and that is a
  legitimate outcome, not a reason to extend.
* Any capital increase. The $2.00 completion budget and the $3.8830 cumulative
  headroom are the existing figures, unchanged.
* Scaling on a pass. Two windows establish mechanism, not expectancy. n=2
  proves the route works; it proves nothing about profitability, and the
  148-window attested structure says the per-pair economics are the open
  question.

---

## Phase B runbook — the exact ordered steps, none of them taken

Written out so the live test is a sequence to execute rather than a decision
to make under time pressure. **Nothing below has been done.** Every step is an
owner action or needs owner authorization first.

### Preconditions, all checkable before anything is written

| # | precondition | how to check | state at time of writing |
|---|---|---|---|
| P1 | Phase A passed, including A1 after the log-bound fix is deployed | `L5_LIVE_TICK_STAGES.stages_s.roll` | **A1 open** — 6.4-6.6 s, root cause fixed on the branch, not deployed |
| P2 | `L5_LIVE_HALT` asserted before any switch is written | `L5_LIVE_HB.halt_local_env` | currently **not asserted** |
| P3 | watchdog sweeping, `read_error: null` | `L5_WATCHDOG_SWEEP` | **PASS**, 20 s cadence |
| P4 | `open_orders_at_venue: 0` and `uncertainty_cleared: true` | `L5_LIVE_RECONCILE` | **PASS** |
| P5 | cumulative headroom still >= $3.00 | `realized_today` vs `L5_MAX_CUMULATIVE_LOSS_USD` | **PASS**, $3.8830 |
| P6 | `defensive_spent_usd` read and recorded as the starting point | volume state | needed; budget is **lifetime**, not per campaign |

### The five variable writes, in this order

Order matters: the floor must exist before the route that reads it can be
authorized, or a rescue refuses with `RESCUE_IMPROVEMENT_FLOOR_UNSET` and the
window is spent on a no-op.

```
1  L5_TAKER_MIN_WORST_IMPROVEMENT_USD = 0.25
2  L5_MAX_COMPLETION_EV_GIVEUP_USD    = 0.05
3  L5_TAKER_LOSS_REDUCTION            = YES
4  L5_TAKER_COMPLETION_POLICY         = YES     (already SET, not YES)
5  L5_TS_COMPLETION_CROSS             = YES     (already SET, not YES)
```

Then, and only then, the campaign mint and the arm. Clearing `L5_LIVE_HALT` is
the **last** step, not the first.

### Why 0.25 and 0.05, from the replay and not from preference

Restating so the numbers are not re-derived under pressure. Both come from
`src/l5_issue195_replay.py` against the recorded books of the two windows that
actually lost money.

| window | best executable improvement | EV given up | floor 0.25 | floor 0.35 | floor 0.50 |
|---|---|---|---|---|---|
| 0300z | **$0.65** | $0.0138 | captures | captures | captures |
| 0315z | **$0.35** | $0.0400 | captures | marginal | **loses** |

A floor above 0.35 discards 0315z outright. A floor at 0.35 catches it only at
its single best moment, which is not a margin. 0.25 captures both windows and
still refuses the marginal tail, which is what a floor is for. The EV cap at
0.05 covers the measured 0.0138-0.0400 with room; a 0.02 cap would discard
0315z to save one cent of modelled give-up.

Neither number is a target. If a window offers nothing above 0.25 the correct
outcome is **no cross**, and that is a pass, not a failed test.

### Rollback, at every step

| if | then |
|---|---|
| any abort condition fires | assert `L5_LIVE_HALT`; watchdog and deadman cancel by exact id; reconcile; report; do **not** re-arm |
| the deploy itself is bad | roll back to the prior deployment (`canRollback: true`) |
| the switches need undoing | set all five back; the two pre-existing ones to their prior non-`YES` values, the three new ones removed |

Setting the switches back is not sufficient on its own if a leg has already
filled: the inventory is real and must be completed or settled, which is what
the completion route and the reconciler are for.
