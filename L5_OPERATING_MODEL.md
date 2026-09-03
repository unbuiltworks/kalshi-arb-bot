# L5 Operating Model — Durable Strategy Handoff

**As of:** 2026-09-03  
**Primary coordination:** GitHub issue #167  
**Trader:** Polymarket `l5zn1bwom8etsk`  
**Purpose:** durable source of truth for the current L5 behavioral thesis, clone rules, promotion gates, and anti-drift constraints. This file is not a realized-P&L ledger and does not override source-accounted execution/audit artifacts.

> If conversation context and this file disagree, re-establish current GitHub/AWS state first, then update this file from source-backed evidence. Do not silently revive superseded hypotheses.

## 1. Core objective

Reverse-engineer L5's sequential decision process and reproduce it only where executable conditions support positive realized expectancy.

Canonical pipeline:

**L5 public activity → normalized history → sequential replay → behavioral model → realtime shadow → action-by-action comparison → bounded live probe → settled/realized audit → controlled scaling**

The core question remains:

> At this exact market state, what causes L5 to choose the next side, price, size, and timing?

Do not reduce the strategy to “buy both sides below $1” or generic arbitrage. Arbitrage/pair economics are an output and safety constraint, not a complete behavioral model.

## 2. Current behavioral thesis

The best-supported current model is:

**L5 is a persistent incremental inventory trader / paid-liquidity-provider, not a directional forecaster.**

Common sequence:

**OPEN → EXTEND → EXTEND / WAIT → COMPLETE or SWITCH → continue working inventory**

Key observed behaviors:

- repeated same-side / nearby-price actions are real and predictive;
- `last_rel` is the strongest missing next-action feature found in held-out work;
- temporary inventory imbalance is tolerated rather than immediately neutralized;
- opposite-side completion is often delayed until pricing improves;
- residual outcome selection has no demonstrated directional skill (~50% winner rate);
- mature behavior is maker-heavy early and more aggressive late where maker/taker is actually measurable;
- incentive-era behavior must not be copied blindly onto an account that is not receiving equivalent incentives.

The live/sports implementation must therefore distinguish:

1. **behavioral policy** — what L5 would likely do next;
2. **venue/execution shell** — what is actually executable/safe here;
3. **economic gate** — whether the action remains positive expectancy without unsupported rebates/rewards.

## 3. Measured historical evolution

Artifact: `analysis/l5/l5_ramp_analysis.json`  
Implementation/report commit: `ef1da90a4ea5954a67e7248beba9993bbd9ad267`

### Mar 19–25 — early era

Small, mostly one-sided activity. Not the mature strategy.

### Mar 26 — systematic-era changepoint

The measured spring activity-scale break occurs **Mar 26**, not Apr 9. This is the first major transition into systematic behavior.

### Mar 27–Apr 5 — developing systematic behavior

Baseline for the subsequent patience improvement.

### Apr 6–10 — the patience upgrade

This period is **not** an activity scale-up.

Compared with Mar 27–Apr 5:

- trades/window fell roughly **23.6 → 14.9**;
- median size stayed roughly **10**;
- opposite-leg wait median increased **16s → 24s**;
- p75 opposite-leg wait increased roughly **30s → 46s**;
- final pair cost improved roughly **0.9851 → 0.9496**;
- within-window pair-cost drift improved from roughly **-0.0016 → -0.0176**;
- complement fill mean fell roughly **0.516 → 0.447**;
- `P(EXTEND | EXTEND)` rose roughly **0.572 → 0.621**;
- same-side run p90 increased **4 → 5**;
- imbalance repair half-life increased roughly **94s → 214s**.

Interpretation: **fewer, more patient actions at better completion prices**. L5 learned to tolerate imbalance longer and price the wait rather than force immediate completion.

### Apr 21 — second measured boundary / first credited maker rebate

A second boundary appears around **Apr 21** in both size and EXTEND persistence. This is also the date of the first credited `MAKER_REBATE` in the accounting ledger.

This is timing evidence, **not proof of causality**.

Later credited incentive milestones:

- first maker rebate: **2026-04-21**
- first taker rebate: **2026-06-20**
- first reward: **2026-08-08**

### Aug 28–31 — mature current behavior

Current-mature behavior adds capabilities not present in spring:

- same-side runs are longer (mean around **3.11**, p90 around **7**);
- `P(EXTEND | EXTEND)` around **0.695**;
- much larger peak imbalance but faster repair;
- opposite-side wait can fall to only a few seconds because flow/liquidity brings the complement quickly;
- final pair cost can approach **~0.995**;
- concurrency reaches multiple simultaneous markets (mean ~3.1, max 8 in measured sample);
- maker share falls sharply near settlement where labels are measurable.

These behaviors all postdate the incentive era and must remain **locked** for our clone unless equivalent economics are source-confirmed on our account.

## 4. Important corrections to earlier working hypotheses

1. **Apr 9 is not the scale changepoint.** Mar 26 is the first measured systematic activity break; Apr 6–10 is a patience/quality upgrade.
2. **“Smaller but more often” is not an across-epoch rule.** Per-fill size generally rose across epochs. Smaller action size appears as a **concurrency effect inside August** (e.g. size falls as simultaneous-window count increases).
3. **Concurrency was not part of the spring strategy.** Spring max concurrency was 1. Multi-market concurrency was added much later.
4. **Spring maker/taker cannot be measured honestly.** Pre-Aug canonical rows lack maker/taker labels; do not proxy or infer them as fact.
5. **Near-$1 pairs are incentive-era behavior.** Spring patience targeted materially cheaper pair completion (~0.95–0.96 range). Do not tolerate ~0.995 pair economics without source-confirmed incentive support.
6. **Directional skill remains unsupported.** Residual-on-winner is approximately coin-flip across regimes.

## 5. Persistence-v2 model

Commit lineage: `b5db1985...` before ramp-analysis commit `ef1da90a...`.

The frozen v1 action model used price-band × TTE × imbalance but did **not** condition on `last_rel`.

Persistence-v2 adds:

- base state + `last_rel` + `last_size`;
- shrinkage/backoff to avoid sparse-state overfit;
- explicit `persist_extend_p` vs `switch_complete_p`;
- proposed relation, side, maker price zone, and reevaluation cadence;
- side-by-side comparison with the frozen v1 and pair gate.

Illustrative populated state from the delivered model:

- price ~45–55¢;
- imbalance 1–10;
- previous relation = `extend`;
- next relation roughly **67.1% EXTEND / 32.9% COMPLETE**.

This is the first model that directly encodes the repeated-increment behavior rather than merely recording it.

Sports TTE remains incomplete until event/end-time metadata is wired accurately; off-distribution states must remain explicit.

## 6. Spring-discipline rules for the penny → incremental clone

These are the current behavioral operating rules unless new L5 evidence overturns them.

### R1 — One market at a time first

Spring concurrency was 1. Do not introduce multi-market live concurrency before the one-market behavior is clean and settled repeatedly.

### R2 — Mid-band, maker-first, no directional thesis

Favor the L5-like mid-band states (historically ~45–55¢ modal spring entry zone) and maker-first execution. Do not buy a side because we believe it will win.

### R3 — Persist in small flat increments

Same-side extension is a first-class action. Prefer increasing **frequency before size**.

### R4 — Do not force completion; price the wait

Apr 6–10's defining improvement was longer waiting for materially cheaper complements. Persistent residual management should outlive an arbitrary 300-second shell when risk and time-to-settlement allow it.

However, public/theoretical BBO availability is not proof of an executable fill. Preserve that distinction.

### R5 — Bounded imbalance

Temporary residual is allowed; unlimited residual is not. Inventory must remain inside explicit dollar/quantity caps and must block unrelated new first legs under the current one-market stage.

### R6 — Rebate-era behaviors stay locked

Do **not** enable merely because mature L5 does them:

- near-$1 pair tolerance;
- 3–8 concurrent markets;
- aggressive late taker completion;
- economics dependent on unconfirmed rebates/rewards.

Unlock only when equivalent incentives are actually credited and source-accounted for our account, followed by replay/shadow validation.

### R7 — Historical scaling order

**PATIENCE → FREQUENCY → SIZE → CONCURRENCY**

This is the default promotion order for our clone.

## 7. PMUS sports/props transfer architecture

Do not focus on one game manually. The intended system is:

**full-slate discovery → normalized per-market state → rotating realtime hot set → per-market L5 state machine → opportunity ranking → global risk allocator → bounded executor**

The full-slate discovery fix proved the previous “no opportunities” state was partly a coverage artifact:

- live discovery has observed well over 100 sports markets with the majority being props;
- old scanning only exposed a narrow subset / subscription cap;
- full-slate eligible-market reporting and rotation/warm-start are now part of the transfer path.

A game/event is a useful context cluster, not the only market the system may watch.

## 8. MICRO-LIVE execution rules

Purpose: obtain real order/fill/queue/settlement evidence with penny-scale exposure while strategy fidelity is still being learned.

The canonical micro executor must have exactly one implementation after reconciliation. Required properties:

- separate `PMUS_MICRO_*` arming keys; micro arming must never arm q=1;
- venue `minimumTradeQty` + tick read live;
- target roughly **$0.01 per action**;
- venue-minimum exception only when bounded; no silent q=1 fallback;
- **≤$0.02 worst-case notional on either possible binary leg**;
- **≤$0.05 cumulative first-leg notional per market** during the current learning stage;
- Decimal quantity preserved end-to-end;
- partial first/complement fills become exact residual inventory, never rounded to 0 or 1;
- account-wide preflight: no open orders and no unrelated nonzero positions;
- preview + fresh BBO checks before mutation;
- mutation calls are never automatically replayed;
- market-scoped cancel/deadman;
- one market/campaign, no auto-repeat for the initial micro probes;
- fees/rebates/rewards tracked separately;
- `realized_pnl` remains null until authoritative settlement/cashflow evidence supports the label.

Live venue evidence has already shown `minimumTradeQty=0.01` on sports markets, allowing real sub-1¢/≈1¢ action notionals.

## 9. Current live-stage promotion policy

Current stage is **bounded MICRO-LIVE learning**, not continuous autonomous scaling.

First persistent incremental live sequence should resemble spring discipline:

**maker OPEN ~1¢ → reassess → optional same-side EXTEND ~1¢ → reassess → patient fee-safe COMPLETE when price improves → settle/audit**

Initial restrictions:

- one market;
- no concurrent live market;
- no unsupported rebate assumptions;
- no near-$1 pair tolerance;
- no automatic late taker just because mature August L5 did it;
- no risk increase after a win;
- account/residual must be source-confirmed clean before a new first leg.

Promotion requires action-by-action behavioral similarity **and** clean executable/settled evidence, not aggregate shadow P&L alone.

## 10. Strict accounting lifecycle

Always preserve:

**historical replay → shadow → live probe → submitted → filled → settled → realized**

Never call any of the following realized:

- theoretical fills;
- stale/public BBO opportunity;
- shadow inventory;
- modeled pair edge;
- unconfirmed rebates/rewards;
- balance movement without sufficient source identity;
- mark-to-market gains;
- an order merely being submitted.

Report separately:

1. matched-pair economics;
2. directional residual P&L;
3. spread capture;
4. maker rebates;
5. liquidity rewards;
6. fees;
7. settlement/redemption cashflows.

## 11. Risk shell

Always bound:

- dollars per action/market;
- directional residual;
- cumulative imbalance;
- daily realized loss;
- concurrent markets;
- total strategy exposure;
- open-order lifetime and deadman cancellation.

A residual must never be hidden by integer conversion. Any nonzero Decimal position is a real position.

## 12. Evidence that remains missing / next P0s

- ~~reconcile PR #169 and transfer-branch micro implementations into exactly one canonical micro executor~~ — **done at `6e0669c1`** (#169 engine + isolated `PMUS_MICRO_*` arming, SKIP semantics, $0.05 durable cap, `micro_eligible`);
- run the bounded `spring_l5` one-market session (`src/pmus_spring_l5_session.py`) per comment 5520504343 once staged DISARMED and reviewed;
- wire accurate sports TTE/event clock into persistence-v2;
- collect realtime persistence-v2 shadow sequences across sports/props;
- validate bucket→executable size/cadence mapping with real micro fills;
- diagnose why real DET/MIN complement execution missed theoretical within-deadline fee-safe windows;
- append newest L5 public activity continuously and run drift checks;
- keep April-ramp conclusions and current-mature behavior separately labeled;
- do not enable intentional multi-market inventory until one-market spring-discipline probes are repeatedly clean.

## 13. Key source references

- Issue #167 — canonical coordination / handoffs.
- `analysis/l5/l5_ramp_analysis.json` — measured ramp/current comparison.
- `analysis/l5/l5_action_model_v2.json` — persistence-aware action model.
- `src/pmus_l5_persistence_v2.py` — persistence-v2 policy/shadow layer.
- `src/pmus_l5_action_shadow.py` — per-market L5 inventory/action state.
- `src/pmus_micro_executor.py` — micro-live executor path (must remain single canonical implementation after reconciliation).
- `src/pmus_settlement_auditor.py` — PMUS settlement/reconciliation authority.
- `L5_STATUS.md` — broader historical pipeline/status; some transfer/live details may lag and should be refreshed against issue #167/current branch before operational use.

## 14. Anti-drift rule

Before adding a new strategy, venue behavior, or risk increase, answer:

> How does this reproduce, validate, deploy, or improve the L5-style engine?

If the answer is unclear, it is probably not the priority.

The target remains **real, executable, settled, source-accounted profit**. The historical $480/day objective is a target to measure against, never an assumption.
