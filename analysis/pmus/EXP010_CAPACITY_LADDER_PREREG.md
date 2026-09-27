# EXP-010 — capacity ladder preregistration (PROBE-CAP-02)

Written at HEAD `8e09f9fa`+, **before** any arming. Nothing here is armed,
deployed, or ordered. Budget lineage in
`analysis/pmus/PROBE_AUTHORIZATION_LEDGER.md`.

## 1. The executor that produced the 5.31-contract fills

**`src/pmus_spring_l5_session.py`**, driven by **`src/pmus_l5_continuous_session.py`**,
deployed as Railway service **`pmus-spring-l5-probe`**
(`deploy/pmus_spring_l5/railway.json`, `startCommand: python3 -m src.pmus_l5_continuous_session`,
`restartPolicyType: NEVER`).

Evidence — `analysis/l5/r4_orders.jsonl`, 64 order rows, 31 sessions,
2026-09-07T08:09Z → 18:33Z, 3 ATP tennis markets. **10 filled orders**, max
**5.31 contracts @ $0.47 = $2.4957** (`CBER077HYQ3R`), next `5.20 @ 0.51`,
`5.24 @ 0.38`, `5.10 @ 0.49`, `5.01 @ 0.51`. Stages `spring_open_*`,
`spring_extend_4`, `spring_complete_*`.

Sizing was the **owner-approved R4 rung** (approval 5567269295, recorded in
`src/l5_restriction_audit.py`): `per_action_target_usd 2.50`, per-leg `3.25`,
`per_market_cap_usd 7.50`, `max_unmatched_usd 2.50`, concurrency 1, maker-only,
horizon 604800s. **Not** the spring default of $0.01/$0.02.

This is the path to reuse. No new executor, no new Railway service.

## 2. Can it safely express q10 / q25 / q50?

**Clip sizing: YES, already configurable.** `src/pmus_micro_executor.py` sizes
each action from `src/pmus_l5_ramp_ladder.py` (`target_notional()`,
`max_leg_notional()`) — the same mechanism that ran $0.01/$0.02 at R1/R2C and
$2.50/$3.25 at R3L/R4. `plan()` already returns `entry_notional` and
**`worst_complement_notional`**, i.e. the exact quantities a risk manifest needs.

**Hard exposure controls: YES, already present and inherited, not reimplemented.**
Isolated dual-key arm, credentials from env only, durable state, daily-loss stop,
account-wide flat/open-order preflight, maker/post-only only, ONE outstanding
order, bounded actions and residual, exact-ID cancel + market-scoped deadman,
`restartPolicyType: NEVER`, mutations never replayed.

**Two blockers, both material:**

**(a) The proven path is MAKER ONLY — "There is no taker code path in spring mode
at all."** The shadow policy this ladder is meant to validate is B′+ECONOMIC_COMPLETE,
whose completions are **taker** at θ=0.0695. The live path therefore **cannot
execute the policy whose economics we measured.** Reusing it tests a maker-only
variant, not EC. Options: accept that the probe validates *fill capacity and
reconciliation only* (recommended), or preregister a taker-completion path
separately (a real strategy change, not in scope here).

**(b) The exposure arithmetic does not fit the envelope.** An unmatched first leg
of q contracts at price p loses **q × p** if it settles against us, and nothing
guarantees the complement fills — our own shadow shows terminal residual in
**3 of 7** windows.

| q | p=0.10 | p=0.20 | p=0.30 | p=0.45 (spring band) |
|---|---|---|---|---|
| 5 | 0.50 | 1.00 | 1.50 | 2.25 |
| **10** | 1.00 | **2.00** | 3.00 | **4.50 — 90% of the whole envelope** |
| **25** | **2.50** | 5.00 | 7.50 | **11.25 — 2.2× the envelope** |
| **50** | 5.00 | 10.00 | 15.00 | **22.50 — 4.5× the envelope** |

At the spring mid-band the ladder is **not fundable at $5.00**. Capping per-market
worst case at $2.00 (40% of the envelope) admits q10 only at **p ≤ 0.20**,
q25 at **p ≤ 0.08**, q50 at **p ≤ 0.04**.

## 3. Proposed per-rung bounds

Envelope $5.00. **$1.00 held back permanently unallocated** → $4.00 allocable.
Two distinct controls, never conflated: **deployed capital** (cash out, recovered
at settlement on a matched pair) and **worst-case realized loss** (unmatched
basis). The $5.00 stop governs the second.

| | **Rung A** | Rung B | Rung C |
|---|---|---|---|
| clip | **q = 10** | q = 25 | q = 50 |
| entry band | **p ∈ [0.10, 0.20]** | p ≤ 0.08 | p ≤ 0.04 |
| per-action target | $1.00–$2.00 | ≤$2.00 | ≤$2.00 |
| **max_unmatched_usd (hard)** | **$2.00** | $2.00 | $2.00 |
| per-market deployed cap | $10.50 | $25.00 | $49.00 |
| concurrency | 1 | 1 | 1 |
| outstanding orders | 1 | 1 | 1 |
| **rung realized-loss budget** | **$1.50** | $1.50 | $1.00 |
| cumulative stop | **−$5.00, never raised** | same | same |
| status | **PROPOSED** | **NOT RECOMMENDED at $5.00** | **NOT RECOMMENDED at $5.00** |

Rung A pass criteria, all required before Rung B is eligible:
1. ≥3 markets with an authoritative fill at **≥10 contracts** in one order.
2. `filled_qty ≥ 0.90 × intended qty` on every fill (partial-fill behaviour
   consistent with the shadow's clip assumption).
3. Settlement reconciled and authoritative cashflow matched — realized only then.
4. Zero accounting or safety discrepancies. Any discrepancy ⇒ **DISARM and diagnose**.
5. Cumulative realized loss within the rung budget.
6. Observed queue behaviour not contradicting the queue-aware model (queue-ahead
   at our exact level, decremented only on price-attributable volume).

**Rungs B and C are preregistered but NOT recommended under PROBE-CAP-02.** They
require entry at p ≤ 0.08 / ≤ 0.04, which is (i) far outside the spring band the
proven path enforces, (ii) the cheap-residual band where L5's own measured
residual is −$0.1584 to −$0.2487 per unit, and (iii) where the maker rebate
rounds to ~$0.02 per fill. They would test capacity *outside* the economic regime
being validated. They need either a larger, separately authorized envelope or an
explicit decision to accept that trade.

## 4. Authenticated state and credentials

**Unavailable. The probe therefore remains DISARMED.**

- This container: no PMUS credentials of any kind.
- `pmus-q1-executor`: only Railway's 8 `RAILWAY_*` built-ins, `sealedVariableNames: []`.
- **The service that previously held and used the credentials is `pmus-spring-l5-probe`** —
  it ran `src.pmus_l5_continuous_session`, which reads `PMUS_API_KEY_ID` /
  `PMUS_API_SECRET_KEY` from env, the same names as the recorder. Its variable list
  could not be read back in this session (the service id would not resolve through
  the Railway MCP); no value was requested, copied, printed, moved or exposed, and
  none will be.

Mandatory before any order, and not satisfiable by me: authenticated verification
of **balances, positions and open orders**. **Orphan orders/positions: NOT
VERIFIED.**

## 5. Artifact that would run

Current HEAD of `claude/l5-pmus-economic-results-7c116e`, which includes every
correctness fix from this session: `1ea8760e` (double-charged completion fee),
`3a92c583` (level-accurate queue model), `7c11bc23`, `19955307`, `94c78d8d`.

The deployed `pmus-spring-l5-probe` and `pmus-q1-executor` builds are from
`feature/l5-transfer-backtest` and predate all of it. **A rebuild from this HEAD
is required before any live run** — otherwise the live lane runs pre-audit logic.
Not performed: no redeploy without explicit instruction.

## 6. Manual operator steps (Claude must not do these)

1. Set on `pmus-spring-l5-probe` (or the chosen service): `PMUS_API_KEY_ID`,
   `PMUS_API_SECRET_KEY`. Prefer **sealed** variables.
2. Set the isolated arm trio: `PMUS_SPRING_LIVE=YES`,
   `PMUS_SPRING_SESSION_ID=<fresh id>`, `PMUS_SPRING_ARM=<same id>`.
3. Set the gate: `PMUS_SPRING_MARKET_SLUG=<slug>` and
   `PMUS_SPRING_GATE_EVIDENCE=<same slug>`.
4. Approve the **Rung A** sizing in `src/pmus_l5_ramp_ladder.py` by the existing
   owner-approval mechanism (as R4 was approved), rather than by editing constants
   ad hoc.
5. Trigger the redeploy through `deploy/pmus_q1_executor/ENABLE_REDEPLOY_BY_OWNER_ONLY/**`
   or the spring equivalent, from this HEAD.
6. Leave `pmus-q1-executor` untouched — it is preserved as the historical
   fail-closed q=1 canary.

The n=25 frozen collector/scorer (`pmus-btc15m-tape` + the hourly routine) is
untouched by all of the above and keeps running.
