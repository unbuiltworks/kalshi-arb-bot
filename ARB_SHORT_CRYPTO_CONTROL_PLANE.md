# ARB Short-Crypto Control Plane

Status: **SHADOW / READ-ONLY / NO ORDER AUTHORITY**

Last established live state: 2026-08-27 ET.

This file is the operational source of truth for the short-duration crypto maker/temporal lane. `ARB_SHORT_CRYPTO_MARKET_MAKING.md` remains the deeper strategy/research record. This file answers: what is live, what gets built next, where it runs, how it is measured, and what must be true before any canary.

## Objective

Build a venue-agnostic short-duration binary-market engine that can run independent income candidates across Polymarket, Gemini Predictions, and Kalshi while keeping venue settlement, fees, incentives, inventory, and P&L separate.

The ARB program target remains at least $480/day REALIZED net profit, but this lane receives **$0 realized-profit credit** until actual fills settle and all venue-specific costs/incentives are reconciled.

## Project / deployment boundary

- Keep this work inside the existing ARB ChatGPT project.
- Keep repository work on `unbuiltworks/kalshi-arb-bot`, isolated on branch `ev-wallet-shadow` / draft PR #160 until promotion gates are met.
- Keep Railway research in the separate project `arb-ev-wallet-shadow`.
- Do **not** consume or alter `sports-arb-autonomous` services.
- Do **not** modify the unrelated SOL executor.
- Do **not** reuse the sports AWS maker host as the production crypto hot path. When AWS promotion begins, use a separately named crypto service/instance/process boundary with independent restart, logs, risk state, and credentials.
- Railway is acceptable for discovery, public-wallet forensics, accounting, slow control-plane jobs, and early shadow collection.
- AWS us-east-1 is the intended hot path for persistent WebSockets, queue/fill inference, cancel latency, fair-value feeds, and eventual canaries.

## First-tier stream matrix

Each row is an independent venue x asset x horizon stream with its own inventory and P&L.

| Venue | BTC | ETH | SOL | XRP | Streams |
| --- | --- | --- | --- | --- | ---: |
| Polymarket | 5m, 15m | 5m, 15m | 5m, 15m | 5m, 15m | 8 |
| Gemini Predictions | 5m, 15m | 5m, 15m | 5m, 15m | 5m, 15m | 8 |
| Kalshi | 15m | 15m | 15m | 15m | 4 |
| **Total** |  |  |  |  | **20** |

Do not net P&L across venues to claim an arb unless settlement compatibility is separately certified. These are intended to be independent maker/inventory engines first.

## What the reference wallet taught us

Reference wallet: `l5Zn1bWoM8eTsK` / `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`.

Current public-data findings:

- Latest 10,000 public fills: **83.28% inferred maker / 16.72% taker** by comparing Polymarket `takerOnly=false` vs `takerOnly=true` responses.
- Same 10,000-fill sample: about **$76.3K maker notional** and **$30.7K taker notional**.
- Seven-day explicit public incentive cashflows: about **$7,733.39 maker rebates** plus **$9,515.66 liquidity rewards**.
- Rolling reconstructed 24h resolved short-crypto ledger at the time of measurement:
  - 5m resolved trading P&L: about **+$2,211.74**.
  - 15m resolved trading P&L: about **-$363.89**.
  - Combined resolved trading P&L: about **+$1,847.85**.
  - Explicit rebate + reward cashflows in the aligned current window: about **+$2,796.47**.
  - Reconstructed resolved trading + incentive economics: about **+$4,644.32**.
- These are **reference-wallet economics**, not user profit and not a capacity estimate.

Working strategy model:

1. Maintain aggressive resting liquidity on both outcomes where incentive economics are attractive.
2. Let maker orders fill in fragments as the book moves through them.
3. Use selective taker orders to rebalance or deliberately skew inventory toward the current fair-value signal.
4. Hold contracts to resolution/redeem rather than depend on frequent mid-window sells.
5. Earn total economics from four buckets: maker spread/fill capture, maker rebates, liquidity rewards, and inventory/directional P&L.
6. Pure temporal sub-$1 pair completion remains a control mechanism, not assumed to be the primary edge.

## Current Railway live state

Project: `arb-ev-wallet-shadow`.

Persistent read-only services:

- `polymarket-wallet-shadow` — public reference-wallet activity/copyability collector.
- `polymarket-temporal-shadow-run` — settlement-aware temporal control shadow.
- `polymarket-maker-econ-shadow` — reward-aware two-sided Polymarket maker shadow.
- `polymarket-l5-econ-ledger` — rolling public resolved trading + reward/rebate ledger.

All active services are read-only and currently deploy successfully. No trading credentials or order mutation path is required for these shadows.

### Polymarket maker-economics shadow

Current design:

- dynamically discovers the current BTC/ETH/SOL/XRP 5m/15m windows;
- reads public liquidity-reward configuration;
- samples only currently active markets;
- parallelizes book reads;
- records reward-eligible quote samples;
- records conservative cross-through fill candidates only after a hypothetical quote has rested;
- never labels a cross-through candidate as a confirmed fill;
- never counts the displayed reward pool as our reward income.

Initial runtime observation after optimization: roughly one second to sample all eight active Polymarket streams on Railway, which is sufficient for research but not the target execution architecture.

## Venue economics and replication

### Polymarket

Primary proven reference venue.

Measure separately:

- maker fill/cross-through rate;
- queue/adverse-selection loss;
- maker rebate income;
- liquidity reward share;
- 5m vs 15m economics;
- BTC/ETH/SOL/XRP economics;
- paired inventory vs residual inventory;
- settlement P&L.

### Gemini Predictions

Gemini is the highest-priority replication venue after Polymarket because the product shape closely matches the reference strategy:

- BTC/ETH/SOL/XRP short-duration prediction contracts support 5m and 15m formats;
- public real-time `bookTicker`, depth, trade, contract-status, and index-price WebSocket streams exist;
- Gemini publishes a Maker Rebate Program for resting fills;
- Gemini publishes a Liquidity Rewards Program for qualifying resting quotes;
- Gemini's reward program explicitly values tight quotes, size, two-sided presence, and uptime;
- the same resting order can potentially earn maker-rebate and liquidity-reward economics independently;
- a separate taker-reward program may change the economics of selective aggressive inventory management while it remains active.

A read-only `src/gemini_prediction_maker_shadow.py` adapter already exists on the branch. It is not live-authorized and should remain public-data-only until tested.

### Kalshi

Kalshi currently contributes four first-tier 15m streams: BTC/ETH/SOL/XRP.

The existing `src/kalshi_temporal_live_shadow.py` provides the first public-data temporal baseline with venue-specific q=1 fee handling and series/settlement-source checks.

Replication should not stop at temporal pairing. Kalshi's regular-member Liquidity Incentive Program rewards resting orders based on size/proximity and relative liquidity contribution, and crypto 15m series are also listed in Kalshi's designated liquidity-provider program. Therefore Kalshi needs a separate reward-aware maker-economics shadow in addition to the temporal control.

## Correct target architecture

Do **not** build three unrelated bots. Build a shared strategy/risk core with three venue adapters.

### Shared core

- market clock / horizon state;
- normalized YES/NO inventory ledger;
- matched vs residual inventory decomposition;
- fair-value probability;
- inventory-skew target;
- maker quote placement policy;
- taker overlay policy;
- reward/rebate estimator;
- per-fill fee model;
- settlement/redeem accounting;
- per-stream capital reservation;
- cross-venue aggregate BTC/ETH/SOL/XRP directional risk cap;
- promotion-state machine: SHADOW -> AWS_SHADOW -> Q1_CANARY -> LIVE_LIMITED -> SCALE.

### Venue adapters

Each adapter owns:

- discovery and lifecycle parsing;
- exact settlement-source metadata;
- public WebSocket market data;
- fee/rebate/reward rules;
- order semantics for eventual execution;
- queue/fill/cancel telemetry;
- venue-specific position and balance reconciliation once authenticated execution is separately authorized.

### Market-data architecture

Hot path:

`persistent WS -> local book -> reference/index price -> fair value -> inventory state -> maker quote decision -> hypothetical/actual order lifecycle`

REST should be reserved for discovery, restart recovery, reward config, reconciliation, and audit snapshots.

## Strategy candidates to measure independently

Never blend them into one flattering P&L number.

### M1 — Reward-aware symmetric maker

Quote both sides close to the midpoint where reward economics justify the inventory risk.

### M2 — Fair-value inventory-skew maker

Use the venue's settlement/reference index plus faster crypto feeds to shift quote prices/sizes toward the side with positive estimated EV while preserving two-sided reward eligibility where possible.

### M3 — Pure temporal pair control

Only attempts to form positive all-in paired inventory over time. This is the low-directionality control strategy.

### M4 — Late-window taker overlay

Aggressive high-confidence buys near expiry only when estimated fair value exceeds executable price after fees/reversal reserve. Measure separately because this carries real directional risk.

The reference wallet may combine M1 + M2 + selective M4. M3 is useful for attribution even if it is not the primary engine.

## Metrics required per stream

Every venue x asset x horizon stream must report:

- book updates / second;
- quote uptime;
- reward-eligible uptime;
- quoted size and distance from midpoint/reference;
- estimated reward score and competitor denominator when observable;
- maker fill candidates;
- confirmed maker fills once canary/live exists;
- taker fills;
- maker/taker ratio;
- queue wait and fill probability;
- markout at 10/50/80/250/500ms and to settlement;
- cancel/replace latency;
- matched quantity;
- residual directional quantity;
- average pair cost;
- inventory P&L;
- maker spread capture;
- maker rebate income;
- liquidity reward income;
- taker reward income where applicable;
- total economic P&L;
- capital employed and turnover;
- max one-sided exposure;
- realistic executable $/day at current depth.

## P&L truth rules

- Public wallet P&L is research evidence only.
- Reward-pool headline amounts are **not** our earnings.
- Cross-through candidates are **not** fills.
- Shadow inventory is **not** realized profit.
- Unresolved/stranded inventory is never silently discarded.
- Reward/rebate estimates remain modeled until the venue actually pays them to our account.
- User REALIZED P&L requires confirmed fill -> settlement/redeem -> actual fees/rebates/rewards reconciled.

## Promotion gates

### Gate 0 — Public-data correctness

Required before relying on a shadow:

- correct market discovery;
- correct horizon/asset mapping;
- correct settlement source;
- correct fee and reward program mapping;
- no order authority.

### Gate 1 — Railway shadow

Required evidence:

- enough complete market windows to estimate fill/adverse-selection behavior;
- settlement-aware P&L;
- reward/rebate attribution kept separate;
- per-stream capacity estimate.

### Gate 2 — AWS hot shadow

Move a venue adapter to AWS when REST/Railway latency materially limits inference.

Requirements:

- persistent WebSocket books;
- queue-aware simulator;
- local state persistence/recovery;
- latency and reconnect telemetry;
- no live orders yet.

### Gate 3 — q=1 canary

Requires separate explicit authorization after decision-grade shadow evidence.

Minimum requirements:

- positive after-fee/reward economics in a meaningful sample;
- bounded one-sided tail risk;
- reliable order/fill/cancel reconciliation;
- settlement and incentive accounting verified;
- venue balance reservation;
- hard per-stream and aggregate crypto exposure limits;
- kill switch and stale-feed cancellation.

### Gate 4 — scale

Scale one stream at a time based on actual realized economics and observed depth. Do not scale merely because the reference wallet does.

## Infrastructure decision

### ChatGPT project

Stay in the existing ARB project. Do not create another project; the control MD and lane boundaries provide the needed separation while preserving project history.

### Railway

Stay in `arb-ev-wallet-shadow`. Do not create another Railway project right now. Add one service per independent shadow/ledger only when observability justifies it.

### AWS

AWS is the correct production/hot-shadow direction, but it should be a **separate short-crypto deployment boundary**, not a modification of the sports-maker experiment. Gemini explicitly documents its public WebSocket service as AWS/us-east-1 optimized, making an in-region AWS deployment particularly attractive. Benchmark actual venue round-trip, market-data age, cancel time, and markout before choosing final placement.

## Immediate build order

1. Keep Polymarket maker-econ + wallet ledger collecting continuously.
2. Deploy and validate the Gemini public WebSocket maker/reward shadow for all 8 Gemini streams.
3. Deploy the existing Kalshi 15m temporal baseline for BTC/ETH/SOL/XRP.
4. Build the Kalshi liquidity-incentive/maker-economics shadow.
5. Replace Polymarket REST book sampling with persistent WebSocket/local-book state.
6. Add settlement/reference-index streams and a common fair-value feature recorder.
7. Add queue-aware fill simulation rather than simple cross-through counting.
8. Produce a daily 20-stream scorecard ranked by total modeled economics and capacity.
9. Move the top streams to a separate AWS short-crypto hot-shadow deployment.
10. Consider q=1 live canaries only after the gates above pass.

## Highest-leverage improvements

1. **WebSockets everywhere.** Market making cannot be judged from multi-second REST loops.
2. **Model the reference index, not generic spot.** Use venue settlement/index feeds as truth and exchange spot as lead signals.
3. **Queue-aware fills.** A crossed hypothetical price is not enough; track displayed size ahead, trade prints, cancellations, and time at level.
4. **Reward denominator estimation.** Headline pools are misleading; estimate our share against competing qualifying liquidity.
5. **Inventory-aware quoting.** Quote size and distance should depend on current residual inventory and fair-value confidence.
6. **Asset/horizon separation.** The reference wallet's recent 5m trading economics were materially better than 15m; never assume one policy fits both.
7. **Central crypto risk budget.** Independent venue workers may all become long the same asset direction simultaneously.
8. **Actual economic attribution.** Trading P&L, rebates, liquidity rewards, taker rewards, and fees must remain separate through settlement.
9. **Capital-efficiency ranking.** Rank by realized/model-supported dollars per dollar of capital and per unit of one-sided risk, not raw P&L alone.
10. **AWS only where it earns its complexity.** Use Railway for control-plane/research and AWS for the latency-sensitive components after measuring the benefit.

## Current decision

The build direction is correct: **shared short-crypto maker/inventory core + independent Polymarket/Gemini/Kalshi workers + separate economic ledgers + AWS hot path after shadow validation**.

The biggest improvement versus the first temporal-only design is that the system now treats **maker rebates and liquidity rewards as first-class economics** and treats **directional residual inventory as an explicit risk bucket rather than hiding it inside paired averages**.
