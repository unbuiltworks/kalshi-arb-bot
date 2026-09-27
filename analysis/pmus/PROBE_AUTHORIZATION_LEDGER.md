# PMUS probe authorization ledger

Append-only. Budgets are never merged, never reset, and never reinterpreted.

## Budget lineage

| id | authorized | scope | status | spent | remaining |
|---|---|---|---|---|---|
| **RESEARCH-LOSS-01** | prior authorization | research/shadow lane | OPEN, untouched | — | **$0.49320** |
| **PROBE-CAP-02** | this session, user-explicit | bounded live capacity/economics probe | **AUTHORIZED, NOT STARTED** | **$0.00** | **$5.00** |

PROBE-CAP-02 is a **separate, new** authorization of up to **$5.00 additional
cumulative REALIZED loss**. It does not draw on, extend, or replace
RESEARCH-LOSS-01, and RESEARCH-LOSS-01's $0.49320 is untouched. Historical
accounting is unmodified.

Hard cumulative realized-loss stop for PROBE-CAP-02: **−$5.00**. Not to be
raised to complete a sequence. "Realized" requires filled **and** settled **and**
authoritative cashflow reconciled — nothing is realized before that.

Intended ladder (not started): **q≈5 → q10 → q25**, each size only after the
previous is clean on authoritative fill/reconciliation. **q50 is not eligible**
until q25 demonstrates that actual fill size and queue behaviour remain
compatible with the shadow assumptions and the cumulative probe is inside the
envelope. No jumping to q50.

## State established 2026-09-27 ~21:00Z (before any arming)

| check | result |
|---|---|
| git HEAD | `94c78d8d`, working tree clean, in sync with `origin/claude/l5-pmus-economic-results-7c116e` |
| Railway `pmus-q1-executor` | `state: live`; latest deployment `0d425dd2` SUCCESS **2026-09-06**; `restartPolicyType: NEVER` (fail-closed, correct — money-moving lanes must not self-relaunch) |
| executor source | branch **`feature/l5-transfer-backtest`**, *not* this working branch; contains none of this session's audited work |
| executor redeploy gate | `watchPatterns: deploy/pmus_q1_executor/ENABLE_REDEPLOY_BY_OWNER_ONLY/**` |
| executor variables | only Railway's 8 built-ins (`RAILWAY_*`). **No `PMUS_API_KEY_ID`, no `PMUS_API_SECRET_KEY`, no arm trio.** `sealedVariableNames: []` |
| this container | **no PMUS credentials of any kind** |
| orphan orders / positions | **COULD NOT BE VERIFIED** — requires authenticated reads |

## Why the probe did not start

Three independent blockers. None was worked around.

**1. The mandatory precondition cannot be satisfied by me.** Confirming "no orphan
orders or positions" needs authenticated balances / positions / open-orders
reads. No credentials are reachable from this container or set on the executor
service. A precondition that cannot be verified is not a precondition that can be
waived.

**2. Arming is operator-only by design.** `src/pmus_q1_executor.py` is DISARMED BY
DEFAULT with dual-key fail-closed arming (`PMUS_Q1_LIVE=YES`,
`PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` matching) plus a separate live gate
(`PMUS_Q1_MARKET_SLUG` must equal `PMUS_Q1_GATE_EVIDENCE`). Secrets come only
from env. The module states: *"Claude never arms this; the operator does."*

**3. The existing proven executor cannot express this ladder.** Its docstring
states the bounds are **constants, not knobs**:

> q=1 per leg; exactly ONE campaign per invocation, no auto-restart or scale;
> first-leg cost <= $0.80 + venue fee; total campaign risk capped at $1.00;
> residual cap 1 contract; $1.00 daily realized-loss stop.

The probe starts at q≈5 and climbs to q25. The executor is **hard-capped at
q=1** with a **$1.00** campaign cap and a **$1.00** daily realized-loss stop.
Running even the first rung requires raising hard risk bounds in the money-moving
path. That is not a defect fix, and it is the strategy/infrastructure change this
authorization excluded. It also means the **$5.00 envelope and the executor's own
$1.00 stop conflict** — the executor would halt at $1.00 regardless of the
authorization.

## What the operator would have to decide

1. Whether to set PMUS credentials and the arm trio (operator-only; Claude must
   not).
2. Whether to authorize a **preregistered change to the executor's hard bounds**
   to permit q>1, or instead run the ladder's first rung at the executor's
   existing q=1 — which is *below* the 5.31-contract already-demonstrated size
   and so tests reconciliation but adds no capacity evidence.
3. Whether the executor should first be rebuilt from the current branch, since
   the deployed build predates every correctness fix in this session
   (`1ea8760e` double-fee, `3a92c583` queue model).

Until those are resolved, PROBE-CAP-02 stays **AUTHORIZED, NOT STARTED, $0.00
spent**. The frozen n=25 shadow collection continues unchanged in parallel.
