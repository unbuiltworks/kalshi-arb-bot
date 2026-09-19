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
