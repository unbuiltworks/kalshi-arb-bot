# L5 CURRENT PRIORITY

Operational north star, as of **2026-09-16**. Read with `L5_STATUS.md`, which
preserves evidence and history; this file states what we are doing now. Never
rewrite historical evidence to fit current priorities.

> **If you are a fresh session:** the remaining blocker is **not** an owner
> arming decision. The old R1→R2→R3 rung/campaign program is **retired**. Do not
> resurrect it. Do not clear a latch or set a dual key to "unblock" anything.

## Destination

One continuously operating L5-style engine, not a sequence of experimental rungs:

**L5 observation → FIDELITY desired inventory → explicit ADAPTED bankroll/risk
scaling → Polymarket US sports execution → transport-level mandate → venue
reconciliation → settlement → realized accounting**, looping across many
eligible markets.

Stop expanding infrastructure unless it is required to reach that.

## P0 — Polymarket US sports

### Live money executor: HALTED, and unauthorized

`l5-r1-probe` (`6111a5d4-…`), deployment `e0e89ca7`, commit `9fc7f32d6c66`:
`SHUTDOWN_VERIFIED`, zero open orders, account flat.

Three independent reasons it cannot trade, two of them in the repository:

1. `analysis/l5/authorization/HALT` exists and is COPYed into the image;
   `shutdown_requested()` is read **before** `armed()`.
2. `analysis/l5/authorization/live_mandate.json` is **absent** → `NO_MANDATE`
   → `GuardedTransport` refuses every `POST /v1/orders`.
3. Spending is admitted at the **wire** against an immutable mandate, by
   economic effect rather than by action name.

The service's `PMUS_SPRING_*` dual key is still set and **is no longer
sufficient on its own**. Do not read it as arming.

### The 2026-09-16 authorization breach — happened, fixed, tested

Authorized for **one market at $0.05**; it traded **11+ markets** and committed
**$19.3295** to one. Two causes, both now structural failures rather than
judgement calls:

- the campaign chain continued on owner approvals dated **2026-09-06**
  (R2C/R2C2/R3L), lifting the per-market envelope $0.05 → $7.50;
- `counts_against_market_cap()` was an **allow-list of stage names** that
  excluded `spring_complete_*`, which on this venue is a BUY of the complement.

Fixed by `src/pmus_live_mandate.py`: an immutable per-authorization mandate
(`authorization_id`, `authorized_at`, `service`, `strategy`,
`strategy_version`, `max_markets`, `max_total_markets`, `max_spend_per_market`,
`max_total_spend`, `max_residual`, `max_daily_realized_loss`, `expiry` — all
required), enforced at the transport, with consumption read from the durable
ledger. Continuation, settlement, restart, resume, `advance_gate` and
historical rung approvals **cannot enlarge or replace it**. An approval must
postdate the mandate; undated is refused.

Evidence: `analysis/l5/live_probe/R1_CANARY_BREACH_2026-09-16.md`,
`analysis/l5/live_probe/EXECUTION_ARCHITECTURE.md`.
Tests: `tests/test_live_mandate_adversarial.py` (replays the breach step by
step), `tests/test_live_mandate_ledger_invariant.py` (accepted notional ≤
authorized envelope at every prefix, measured independently from the ledger,
with a control proving the pre-incident rule violates it),
`tests/test_live_mandate_deployment.py`. **Preserve all three.**

### Automatic rung progression is RETIRED

No R4/R5. No automatic R1→R2→R3. A market beyond `max_total_markets` requires a
**new explicit authorization**, which means a human and a commit.

### V1 continuous-inventory SHADOW: RUNNING, unchanged

`l5-v1-shadow` (`8a7bc9f4-…`), deployment `17d027dd`, commit `76061d06`. No
credentials, no transport, no order verb, no executor in the image. ~46 sports
markets/cycle, ~49k observation rows. **Leave it running. Do not tune k, the
pair target, thresholds, the model or Envelope A during this sample.**

Both state lookups are preserved on every row — naive inventory state and the
corrected `residual / k` L5-equivalent state — because the lift changes the
answer on **98%** of rows and we keep measuring that.

### ACT vs WAIT is the current fidelity question

V1 proposes on ~79.5% of observed rows; every WAIT is a risk cap, never the
model. Diagnosis in `analysis/l5/v1_shadow/ACT_VS_WAIT.md`. In short:

- **The model cannot say WAIT.** Its relation vocabulary is
  `{open, complete, extend}`; no wait/hold/none/noop token exists anywhere in
  the artifact. It is a *transition* model over 69,646 L5 **fills** — "given L5
  just acted, what next?" — so `P(act) = 1` by construction. This is the root
  cause, and it is a **missing-NO-ACTION-training-states** problem.
- **A separate implementation defect**: `observe()` never passes
  `last_rel`/`last_size`, so every lookup uses the defaults `(open, q<5)`,
  reaching **40 of 988** full keys and **202 of 69,646** transitions — 0.29% of
  L5's evidence. This degrades *which* action is predicted; it does not cause
  the over-action.
- Waiting **is** represented in the artifact, as `cadence_modal`
  (`gap<2s` / `gap2-10` / `gap10-30` / `gap>30`) — the inter-action gap. V1
  does not consult it.

Do **not** change the controller on this yet.

## Scaling hypothesis — frequency and breadth, not size

From 763,966 unique historical fills. L5's continuous-inventory shape is
already present in the early **2026-03-19 → 04-19** regime: ~218 fills/day,
~$1,139 notional/day, ~$128.69 median window notional, ~28 fills/window,
~10-share typical fill, ~$32.90 median peak residual.

Across the later 87× capital increase, median fill size moved only ~1.4× while
fills/day moved ~44.5× and markets/day ~24×.

**Working hypothesis, supported by that historical comparison and not a
universal rule:** L5 scaled primarily by frequency and breadth. Production
direction: *one L5 controller → explicit bankroll envelope → many eligible
markets → more repetitions as realized evidence earns expansion.* Favour more
eligible markets before materially larger orders.

**No live scaling authorization currently exists.** Candidate envelopes are in
`analysis/l5/v1_shadow/ACT_VS_WAIT.md`; they are unranked and unarmed.

## Accounting

`historical → shadow → submitted → filled → settled → realized`, kept separate.
Cohorts stay separate (`_cohort` is stamped per service on every ledger row).

- R1 cohort venue-confirmed realized: **−$0.0916** across five markets.
- Legacy PMUS cohort: **−$44.4898** across 54 markets. The two never merge.
- `aec-itfme-conbra-andpet-2026-09-16` is **SETTLED_REALIZED_UNCONFIRMED** and
  stays out of every net. Its ~$1.1105 matched-pair figure is **derived
  arithmetic, not realized profit**; its $0.1200 incentive is venue-credited
  and stays separate from trading P&L.

Rebates, rewards and incentives remain separate and attributable. Pending
incentives are accrued, never income.

## Everything else

- **Legacy PMUS** `pmus-spring-l5-probe` (`d84d9a68-…`): stays **HALTED**. Do
  not clear the latch. Its startCommand rewrites source with `sed -i` at boot,
  so the running code is not the reviewed artifact.
- **Gemini**: P1, collecting, untouched.
- **Polymarket.com**: observation/reference only.
- No generic arbitrage, no R/S/D or cap5/8/13 revivals, no new rung strategies.

## What would actually move us forward

A. V1 ACT/WAIT — done, diagnosed; controller unchanged pending a decision.
B. Deployed mandate boundary — verified on commit `9fc7f32d`.
C. Smallest shape-preserving continuous envelope — candidates offered, owner
   chooses.
D. Open question: whether a single $0.05 plumbing regression adds anything the
   adversarial and ledger-prefix tests do not already establish.
