# L5 Prediction-Market Trading System — Claude Instructions

**Branch scope:** `feature/l5-transfer-backtest` and related L5 work.

This file intentionally supersedes the legacy Kalshi-arbitrage-first instructions on this L5 branch. The durable behavioral source of truth is `L5_OPERATING_MODEL.md`; current coordination/evidence lives in GitHub issue #167 and `L5_STATUS.md`.

## Primary objective

Reverse-engineer Polymarket trader `l5zn1bwom8etsk` ("L5"), reproduce the underlying sequential decision process as faithfully as public data allows, validate it historically and in realtime shadow mode, and deploy adapted versions only where executable conditions support positive realized expectancy.

Historical target: **$480/day realized net profit**. This is a target to measure against, never an assumption.

## Strategy priority

L5 replication is the main research and engineering program.

Do **not** redefine the strategy as generic arbitrage, market making, rebate farming, or directional forecasting. Arbitrage/pair economics, spread capture, and rebates can contribute to profitability, but they are components of the economics around the L5 behavioral policy rather than the policy itself.

The canonical question at every state is:

> **Given the current market state and inventory, what causes L5 to choose the next side, price, size, and timing?**

## Current L5 behavioral thesis

The best-supported model is a **persistent incremental inventory trader / paid-liquidity-provider**.

Typical sequence:

**OPEN → EXTEND → EXTEND / WAIT → COMPLETE or SWITCH → continue working inventory**

Implementation implications:

- Enter incrementally when the state resembles L5's historical operating regime.
- Same-side `EXTEND` is a first-class action, not an error to flatten immediately.
- Temporary imbalance is tolerated within explicit risk bounds.
- Opposite-side `COMPLETE` is often delayed until pricing improves.
- Rebalancing must be patient and evidence-driven rather than forced on an arbitrary timer.
- Do not assume residual directional exposure has predictive skill; historical evidence does not support that.
- Favor increasing **frequency before size**.
- Initial live replication remains **one market at a time**.

Spring promotion order:

**PATIENCE → FREQUENCY → SIZE → CONCURRENCY**

## Pair economics / arbitrage

Matched inventory is economically important, but do not simplify L5 into "buy YES + NO below $1."

For each sequence, account separately for:

1. matched-pair economics;
2. directional residual P&L;
3. spread capture;
4. maker rebates;
5. liquidity rewards;
6. fees;
7. settlement/redemption cashflows.

When incremental actions create a fee-safe combined pair cost materially below $1, that produces locked pair economics. This is an arb-like benefit of the sequence, not the sole trigger for the strategy.

Spring-discipline currently prefers materially cheaper pair completion (roughly `<= 0.96` where the venue implementation uses that target). Near-$1 pair tolerance belongs to later incentive-era L5 behavior and stays locked unless equivalent economics are source-confirmed for our account.

## Rebates and rewards

Rebates/rewards are additive economics, never permission to make an otherwise bad trade look profitable.

- Do not assume a rebate or liquidity reward until it is actually credited and source-accounted.
- Keep maker rebates, taker rebates, and rewards separate from trading P&L.
- Do not unlock mature incentive-era behavior merely because L5 used it later.

## Canonical research/deployment pipeline

Maintain one pipeline:

**L5 public activity → normalized history → sequential replay → behavioral model → realtime shadow → action-by-action comparison → bounded live probe → settlement audit → controlled scaling**

Reconcile duplicate implementations instead of starting over.

Before new implementation work, inspect existing L5 branches and issue #167. Preserve existing evidence and provenance.

## Validation standard

The key test is:

> **At this exact market state, what did L5 do next, and what would our clone have done?**

Compare:

- side;
- timing;
- size;
- price;
- inventory state;
- aggressiveness;
- pair economics;
- residual behavior;
- eventual fill/settlement outcome.

Positive aggregate replay P&L with materially different behavior is not sufficient evidence of replication.

## Live progression

Advance only through:

1. historical replay;
2. out-of-sample replay;
3. realtime shadow;
4. action-by-action comparison;
5. bounded live probe;
6. repeated clean probes;
7. controlled scaling.

Never increase risk merely to make results look better.

## Current bounded spring probe discipline

Initial live sequence should resemble:

**maker OPEN small → reassess → optional same-side EXTEND small → reassess → patient fee-safe COMPLETE when price improves → settle/audit**

Current restrictions include:

- one market;
- one outstanding order;
- maker-first;
- no unsupported rebate assumptions;
- no automatic late taker behavior copied from mature August L5;
- no new first leg while unrelated residual exposure exists;
- cumulative first-leg/extend notional tightly bounded for the learning stage;
- no automatic repeat after a completed/crashed live probe;
- settlement-only realized P&L labeling.

Read-only collectors/recorders should self-recover and run 24/7. Money-moving bounded sessions should fail closed and should **not** automatically relaunch themselves after a crash or completion.

## Market/category scope

The engine must remain market-agnostic. Do not assume L5 transfer is crypto-only.

Discovery should allow crypto, esports, baseball, basketball, football, hockey, soccer, tennis, props, economics, politics, commodities, weather, culture, and other lawful prediction-market categories when executable evidence supports them.

Current PMUS architecture:

**full live-slate discovery → cheap screening → HOT/WARM/COLD observation queue → expensive L5 state on top candidates → single-market execution selector**

Hundreds of markets competing for a few observation/execution slots is desirable. Do not manually narrow the system to one event type unless evidence supports that exclusion.

## Profit accounting lifecycle

Always use strict labels:

**historical replay → shadow → live probe → submitted → filled → settled → realized**

Never call any of the following realized profit:

- theoretical fills;
- stale/public BBO opportunities;
- modeled pair profit without fills;
- shadow inventory;
- mark-to-market gains;
- estimated rebates/rewards;
- unconfirmed fills;
- balance changes without sufficient source identity.

## Risk and infrastructure

Always bound:

- dollars per market;
- residual directional exposure;
- inventory imbalance;
- daily realized loss;
- concurrent markets;
- total strategy exposure.

Use cancellation/deadman protections where appropriate.

For infrastructure work:

- establish current GitHub/deployment state first;
- identify active branch/commit;
- preserve unrelated services;
- keep unrelated SOL infrastructure separate;
- never expose credentials;
- do not make infrastructure the workstream when research/execution evidence is the real bottleneck.

## Anti-drift rule

Before starting a new strategy workstream ask:

> **How does this help reproduce, validate, deploy, or improve the L5-style engine?**

If it does not, it is probably not the priority.

Do not repeatedly rerun failed Gemini experiments without a materially new hypothesis. Do not redirect toward generic arbitrage simply because the repository name contains `arb`.

## Durable references

**Gemini BTC5m/BTC15m current-state source of truth:
`analysis/gemini/GEMINI_L5_CURRENT.md`**

Read these before redefining strategy or live progression:

- `L5_OPERATING_MODEL.md` — current behavioral thesis and operating rules; primary durable source of truth.
- `L5_STATUS.md` — current implementation/evidence status.
- `L5_STRATEGY_FINDINGS.md` — research findings.
- GitHub issue **#167** — current coordination, deployment, and acceptance evidence.

If conversation context, this file, and current deployment evidence disagree, re-establish current GitHub/deployment state and update the durable files from source-backed evidence. Do not silently revive superseded hypotheses.
