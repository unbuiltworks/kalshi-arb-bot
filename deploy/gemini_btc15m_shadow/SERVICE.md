# gemini-btc15m-shadow — continuous BTC15m L5 shadow

**SHADOW / DISARMED.** No order path, no authorization id, no arming
environment variable. `shadow_soak` constructs no session and never references
`Driver.place` or `Driver.cancel`; `test_the_soak_still_constructs_no_session_
and_no_mutation_verb` asserts that against the module's AST rather than
trusting a flag.

## What it does

One contract after another, for ever (`--windows 0`):

    15m contract discovered -> pre-entry snapshot -> frozen Model A quotes
    -> public tape and book processed -> inventory updated -> COMPLETE/WAIT
    -> settlement -> next 15m contract

## Why it is not a parallel pipeline

It runs the **same module** as the 5-minute lane. The contract family is a
parameter: `contract_cohort.py` supplies the ticker prefix, the minute grid,
the entry band and the wait budget. `metadata.current_ticker` is shared, and
`current_btc5m_ticker` still answers exactly what it always did — pinned by
`test_the_five_minute_path_is_UNCHANGED_by_the_refactor`.

## What is preserved unchanged

Canonical execution reconciliation, crossed-book refusal, coherent fill reads,
one-contract binding, exposure and settlement accounting, terminal
cancellation and the deadman all come from the same frozen modules the 5m lane
uses. **The frozen policy's TTE bounds are NOT scaled per family**:
`NO_NEW_OPEN_EXTEND_TTE_S` (60s) and `WITHDRAW_ALL_TTE_S` (30s) are absolute
margins from expiry and are identical on both — scaling them would be changing
Model A.

## The one behavioural difference, and why it is safe

In SHADOW, a refusing rollover gate is **recorded and rolled past** instead of
ending the run. The gate exists to stop a new window opening while the last
may still have live orders; in SHADOW no session exists, so that hazard cannot
occur. The relaxation sits inside an explicit `runner.mode == SHADOW` guard and
a test pins that a LIVE runner still stops. Without it an unreadable account
half ends a continuous collector after one window.

## The adapter

`GEMINI_RECYCLE`: while residual exists, EXTEND is suppressed and COMPLETE is
worked. **What frozen Model A wanted is recorded on every cycle**
(`model_a_wanted`, `vetoed_by_adapter`), so fidelity stays measurable.

## Restart policy

`ALWAYS`, which is correct for a read-only collector and is **not** the
money-moving policy — the bounded live probe uses `NEVER` and that is
unchanged. This service can place nothing, so relaunching it cannot repeat a
trade.

## 2026-09-19 — the start command lives in the SERVICE, not in this directory

`railway.json` here is **documentation**. Railway reads a config file from the
repository root by default and this one is at
`deploy/gemini_btc15m_shadow/railway.json`, so nothing reads it: the service's
own `deploy.startCommand` is what actually runs, and it overrides the
Dockerfile `CMD` as well.

Changing the Dockerfile CMD and this railway.json to run both cohorts left the
service still launching the single-cohort BTC15m command, and the give-away was
the banner: the dual path prints `DUAL_COHORT_SHADOW` and a `COHORT ...
evidence=...` line per family, while the old single path prints one bare
`COHORT BTC15m ...`. A `redeploy` did not fix it either — it re-runs the
previous deployment and carried the old command with it.

The live command is now:

```
python3 -m src.gemini_execution.shadow_soak --cohorts btc5m,btc15m \
  --policy GEMINI_RECYCLE --windows 0 --cycles 110 --interval 8.0 \
  --state-root /state
```

`src/gemini_execution/hypothetical.py` was also missing from the service's
watchPatterns, so a change to the fill ledger alone would not have redeployed
this lane. It is there now.

## The corrected-timing arm (2026-09-19)

BTC15m now runs TWO decision records per cycle in this one process:

| arm | spec | `quote_between_tte_s` |
|---|---|---|
| control | `L5_ADAPTED_GEMINI_CRYPTO` (frozen, unchanged) | `[0, 300]` |
| correction | `L5_ADAPTED_GEMINI_BTC15M_V2` | `[0, 900]` |

They see the same tape, book and depth every cycle and share nothing else —
each owns its guard, inventory and hypothetical book. The arm is strictly
additive: it never feeds the control, it cannot stop a window (a raise is
recorded and the loop continues), `_step_arm` is synchronous with no session
and no order path, and `mutations_sent` stays 0.

Per cycle the evidence row carries `correction_arms[label]`; the per-window
record and the `L5_COHORT_HB` heartbeat carry the arm's counters under
`arms`, never summed into the control's.

**BTC5m has no arm** — its window is 300s, so both bands are the same band
there.

**The BTC15m entry band moved 240–280 → 840–880.** An arm quoting `[0, 900]`
on a lane that attaches at tte 280 produces no evidence, and its silence
would read as a finding about the correction rather than about the schedule.
`--cycles 110 --interval 8.0` already covers 880s, so the start command is
unchanged. BTC5m's band is untouched.

**watchPatterns.** `src/l5_surface/*.py` and `artifacts/l5_surface/*.json`
were added — the arm's spec and the frozen artifacts live there, and a
spec-only change would otherwise not redeploy. `deploy/gemini_btc15m_shadow/**`
was narrowed to `.../Dockerfile`: the wildcard matched THIS FILE, so editing
the documentation rebuilt the image and restarted a running collector
mid-window. A doc is not a deployment.

Rationale and evidence: `BTC15M_TIMING_CORRECTION_1.md`.
