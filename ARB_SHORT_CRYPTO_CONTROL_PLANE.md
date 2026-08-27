# ARB Short-Crypto Control Plane

Status: **AWS SHADOW / READ-ONLY / NO ORDER AUTHORITY**

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
- The AWS short-crypto stack now runs in a separately named worktree/container boundary from the sports AWS maker process.
- Railway remains useful for discovery, public-wallet forensics, accounting, slow control-plane jobs, and comparison/fallback shadow collection.
- AWS us-east-1 is now the primary hot-shadow path for persistent WebSockets, queue/fill inference, fair-value feeds, and eventual canaries.

## AWS live checkpoint — 2026-08-27

The short-crypto AWS hot shadow was started successfully from commit `aad72fabc2bc5e63754b017a5bb4b5bbc21d31ce` in `BOT_MODE=paper`.

Running isolated containers:

- `arb-short-poly-maker-ws` — Polymarket BTC/ETH/SOL/XRP x 5m/15m public CLOB WebSocket maker/reward shadow.
- `arb-short-gemini-maker` — Gemini BTC/ETH/SOL/XRP x 5m/15m 100ms depth maker/reward shadow.
- `arb-short-gemini-fair-value` — Gemini Kaiko settlement-index/strike/fair-value shadow for all 8 Gemini streams.
- `arb-short-kalshi-temporal` — Kalshi BTC/ETH/SOL/XRP x 15m temporal control shadow.
- `arb-short-kalshi-incentive` — Kalshi BTC/ETH/SOL/XRP x 15m incentive/top-book shadow.

AWS worktree: `/opt/arb/aws-short-crypto-shadow`.

All five containers are read-only. No live order authority is enabled. This checkpoint receives **$0 REALIZED P&L credit**.

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

Persistent read-only services include the public reference-wallet activity collector, settlement-aware temporal control, reward-aware Polymarket maker shadow, rolling reference-wallet economic ledger, Gemini maker shadow, Gemini fair-value shadow, Kalshi temporal shadow, and Kalshi incentive shadow.

Railway is no longer the preferred short-crypto hot path. It remains the control-plane/research/fallback comparison layer while AWS becomes the primary latency-sensitive shadow host.

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
- public real-time depth and index-price WebSocket streams exist;
- Gemini publishes Maker Rebate and Liquidity Rewards programs;
- the same resting order may contribute to maker-rebate and liquidity-reward economics independently;
- a separate taker-reward program may alter selective aggressive inventory economics while active.

The AWS stack now runs both the maker book shadow and a separate Kaiko settlement-index/strike fair-value process for all 8 Gemini streams.

### Kalshi

Kalshi contributes four first-tier 15m streams: BTC/ETH/SOL/XRP.

The AWS stack currently runs both the temporal control baseline and a separate incentive/top-book shadow. Authenticated WebSocket order-book deltas remain the next market-data upgrade when secure Kalshi credentials are deliberately added to the isolated crypto hot path.

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
- public WebSocket market data where available;
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

### Gate 1 — Railway/control-plane shadow

Required evidence:

- enough complete market windows to estimate fill/adverse-selection behavior;
- settlement-aware P&L;
- reward/rebate attribution kept separate;
- per-stream capacity estimate.

### Gate 2 — AWS hot shadow

**Current lane state.**

Requirements:

- persistent WebSocket books where available;
- queue-aware simulator;
- local state persistence/recovery;
- latency and reconnect telemetry;
- no live orders yet.

The five-container AWS short-crypto stack is now running. Gate 2 is not considered complete until each process proves fresh live data and the unified per-stream scorecard is producing decision-grade economics.

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

Stay in the existing ARB project.

### Railway

Keep `arb-ev-wallet-shadow` as the research/control-plane/fallback environment. Do not use Railway quirks as the blocker for the hot path.

### AWS

AWS is now the primary short-crypto hot-shadow environment. The deployment is intentionally isolated from the sports-maker AWS worktree and containers. Benchmark actual venue market-data age, reconnect quality, queue/fill inference, and markout here before any live-order promotion.

## Immediate build order

1. Verify all five AWS containers are not only running but emitting fresh live summaries.
2. Add a shared 20-stream scorecard across the AWS outputs.
3. Add queue-aware fill simulation to Polymarket/Gemini rather than simple cross-through counting.
4. Add authenticated Kalshi WebSocket local books when secure credentials are available in the isolated crypto deployment.
5. Add common fair-value feature recording across venue settlement/index truth plus faster lead feeds.
6. Add cross-venue aggregate BTC/ETH/SOL/XRP directional-risk limits.
7. Produce daily per-stream economics and capacity estimates.
8. Consider q=1 live canaries only after the gates above pass.

## Highest-leverage improvements

1. **WebSockets everywhere.** Market making cannot be judged from multi-second REST loops.
2. **Model the reference index, not generic spot.** Use venue settlement/index feeds as truth and exchange spot as lead signals.
3. **Queue-aware fills.** A crossed hypothetical price is not enough; track displayed size ahead, trade prints, cancellations, and time at level.
4. **Reward denominator estimation.** Headline pools are misleading; estimate our share against competing qualifying liquidity.
5. **Inventory-aware quoting.** Quote size and distance should depend on current residual inventory and fair-value confidence.
6. **Asset/horizon separation.** Never assume one policy fits all 20 streams.
7. **Central crypto risk budget.** Independent venue workers may all become long the same asset direction simultaneously.
8. **Actual economic attribution.** Trading P&L, rebates, liquidity rewards, taker rewards, and fees must remain separate through settlement.
9. **Capital-efficiency ranking.** Rank by realized/model-supported dollars per dollar of capital and per unit of one-sided risk, not raw P&L alone.
10. **AWS-first hot path, Railway control plane.** Keep the latency-sensitive strategy on AWS and the slower research/accounting jobs on Railway.

## Current decision

The build direction is confirmed: **shared short-crypto maker/inventory core + independent Polymarket/Gemini/Kalshi workers + separate economic ledgers + AWS as the primary hot path**.

The biggest improvement versus the first temporal-only design is that the system now treats **maker rebates and liquidity rewards as first-class economics** and treats **directional residual inventory as an explicit risk bucket rather than hiding it inside paired averages**.
